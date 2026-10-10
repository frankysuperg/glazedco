"""Recomendaciones de reabastecimiento por sucursal a partir del pronóstico existente.

Fórmulas (unidades = piezas; no se recalculan modelos):
- sigma_lt = desviacion_serie × √(lead_time_semanas); si n_residuos < min_residuos,
  se usa sigma_relativa_categoria × demanda_semanal_media_pronosticada.
- stock_seguridad = z × sigma_lt
- ROP = demanda_esperada_durante_lead_time + stock_seguridad
- stock_proyectado(t) = existencias + en_tránsito − demanda_acumulada_hasta_t
- fecha_pedido = primera fecha con stock_proyectado <= ROP
  (el ROP ya incluye la demanda del lead time: no se resta otra vez)
- cantidad = max(0, cobertura_objetivo + stock_seguridad − existencias − en_tránsito),
  redondeada hacia arriba a múltiplos de case_size; en perecederos la cobertura
  en días no supera shelf_life_days.
- riesgo_quiebre = días de cobertura frente al lead time
- riesgo_exceso = semanas de cobertura sobre umbral_exceso_semanas
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .config import ReplenishmentConfig
from .demand import daily_rates_from_forecast, demand_during_days
from .lead_time import estimate_supplier_lead_times

OUTPUT_COLUMNS = [
    "sku", "store_id", "cantidad", "fecha_pedido",
    "riesgo_quiebre", "riesgo_exceso", "motivo",
    "stock_seguridad", "fuente_desviacion", "valores_por_defecto",
]


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _ceil_multiple(quantity: float, case_size: int) -> int:
    if quantity <= 0:
        return 0
    size = max(int(case_size), 1)
    return int(math.ceil(quantity / size) * size)


def _category_series(products: pd.DataFrame, config: ReplenishmentConfig) -> pd.Series:
    if config.category_column in products.columns:
        return products[config.category_column].astype("string").fillna("__sin_categoria__")
    if "is_perishable" in products.columns:
        flag = pd.to_numeric(products.is_perishable, errors="coerce").fillna(0).eq(1)
        return flag.map({True: "perecedero", False: "no_perecedero"}).astype("string")
    return pd.Series(["__sin_categoria__"] * len(products), index=products.index, dtype="string")


def category_desviaciones(
    errors: pd.DataFrame,
    products: pd.DataFrame,
    config: ReplenishmentConfig,
    observed_demand: pd.DataFrame | None = None,
) -> dict[str, float]:
    """Mediana por categoría de std(residuos / media observada de la serie).

    Para una media constante positiva m, std(e/m)=std(e)/m: podemos usar el
    resumen existente de residuos sin recalcular modelos. No se normaliza
    por el pronóstico; medias observadas cero/ausentes no aportan al respaldo.
    """
    if observed_demand is None:
        return {}
    errors = errors.merge(observed_demand, on=["store_id", "sku"], how="left", validate="one_to_one")
    frame = errors.merge(
        products[["sku"]].assign(categoria=_category_series(products, config)).drop_duplicates("sku"),
        on="sku",
        how="left",
    )
    frame["categoria"] = frame.categoria.fillna("__sin_categoria__")
    frame["n_residuos"] = pd.to_numeric(frame.n_residuos, errors="coerce")
    deviation = pd.to_numeric(frame.desviacion, errors="coerce")
    observed = pd.to_numeric(frame.demanda_observada_media, errors="coerce")
    frame["relativa"] = deviation / observed.where(observed.gt(0))
    usable = frame.loc[
        frame.n_residuos.ge(config.min_residuos)
        & np.isfinite(frame.relativa) & frame.relativa.ge(0)
    ]
    if usable.empty:
        return {}
    return {
        str(cat): float(group.relativa.median())
        for cat, group in usable.groupby("categoria", dropna=False)
    }


def _risk_stockout(days_cover: float, lead_time_days: float) -> str:
    if np.isnan(days_cover) or not np.isfinite(lead_time_days) or lead_time_days <= 0:
        return "desconocido"
    ratio = days_cover / lead_time_days
    if ratio < 1.0:
        return "alto"
    if ratio < 1.5:
        return "medio"
    return "bajo"


def _risk_excess(weeks_cover: float, threshold: float) -> str:
    if np.isnan(weeks_cover):
        return "desconocido"
    return "alto" if weeks_cover > threshold else "bajo"


def _unit_margin(products: pd.DataFrame, prices: pd.DataFrame | None, as_of: date) -> pd.DataFrame:
    """Margen unitario = precio vigente − unit_cost; sin inventar costos."""
    base = products[["sku"]].drop_duplicates("sku", keep="first").copy()
    prod = products.drop_duplicates("sku", keep="first")
    if "unit_cost" in prod.columns:
        base["unit_cost"] = pd.to_numeric(prod.unit_cost, errors="coerce").to_numpy()
    else:
        base["unit_cost"] = np.nan
    if "list_price_base" in prod.columns:
        base["unit_price"] = pd.to_numeric(prod.list_price_base, errors="coerce").to_numpy()
    else:
        base["unit_price"] = np.nan
    if prices is not None and not prices.empty and {"sku", "unit_price"}.issubset(prices.columns):
        p = prices.copy()
        p["unit_price"] = pd.to_numeric(p.unit_price, errors="coerce")
        if "effective_from" in p.columns:
            p["effective_from"] = pd.to_datetime(p.effective_from, errors="coerce")
            p = p.loc[p.effective_from.isna() | p.effective_from.dt.date.le(as_of)]
        if "effective_to" in p.columns:
            p["effective_to"] = pd.to_datetime(p.effective_to, errors="coerce")
            p = p.loc[p.effective_to.isna() | p.effective_to.dt.date.ge(as_of)]
        sort_cols = [c for c in ("effective_from",) if c in p.columns]
        latest = p.sort_values(sort_cols, kind="stable").drop_duplicates("sku", keep="last") if sort_cols else p.drop_duplicates("sku", keep="last")
        merged = base[["sku"]].merge(latest[["sku", "unit_price"]], on="sku", how="left")
        base["unit_price"] = merged.unit_price.where(merged.unit_price.notna(), base.unit_price)
    base["unit_margin"] = pd.to_numeric(base.unit_price, errors="coerce") - pd.to_numeric(base.unit_cost, errors="coerce")
    return base


def _first_order_date(
    demand_frame: pd.DataFrame | None,
    on_hand: float,
    rop: float,
    fallback: date,
) -> date:
    if demand_frame is None or demand_frame.empty:
        return fallback
    cumulative = 0.0
    for day_row in demand_frame.itertuples():
        cumulative += float(day_row.daily_demand)
        if on_hand - cumulative <= rop:
            return _as_date(day_row.date)
    return fallback


def recommend_replenishment(
    forecast: pd.DataFrame,
    errors: pd.DataFrame,
    inventory: pd.DataFrame,
    products: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    suppliers: pd.DataFrame,
    config: ReplenishmentConfig,
    prices: pd.DataFrame | None = None,
    observed_demand: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Genera recomendaciones a nivel sucursal con columnas fijas de salida."""
    as_of = _parse_date(config.as_of_date)
    forecast_start = _parse_date(config.forecast_start_date)
    fallback_order_date = as_of + timedelta(days=1)
    daily = daily_rates_from_forecast(forecast, as_of=as_of, forecast_start=forecast_start)
    lead = estimate_supplier_lead_times(purchase_orders, suppliers, config)
    cat_sigma = category_desviaciones(errors, products, config, observed_demand)

    prod = products.drop_duplicates("sku", keep="first").copy()
    prod["categoria"] = _category_series(prod, config)
    for column in ("case_size", "shelf_life_days", "is_perishable", "inventory_tracked", "unit_cost"):
        if column not in prod.columns:
            prod[column] = np.nan
        else:
            prod[column] = pd.to_numeric(prod[column], errors="coerce")
    prod["is_perishable"] = prod.is_perishable.fillna(0).eq(1)
    prod["inventory_tracked"] = prod.inventory_tracked.fillna(0).eq(1)
    if "supplier_id" not in prod.columns:
        prod["supplier_id"] = pd.NA
    prod = prod.merge(lead, on="supplier_id", how="left")
    missing_lt = prod.lead_time_days.isna()
    prod.loc[missing_lt, "lead_time_days"] = config.default_lead_time_days
    prod.loc[missing_lt, "lead_time_source"] = "default_config"
    prod["fill_rate"] = pd.to_numeric(prod.fill_rate, errors="coerce")
    missing_fill = prod.fill_rate.isna() | prod.fill_rate.le(0)
    prod.loc[missing_fill, "fill_rate"] = config.default_fill_rate
    prod.loc[missing_fill, "fill_rate_source"] = "default_config"

    inv = inventory.copy()
    inv["date"] = pd.to_datetime(inv.date, errors="coerce")
    cutoff = inv.loc[inv.date.dt.date.eq(as_of)].copy()
    cutoff["inventario_presente"] = True
    cutoff["existencias"] = pd.to_numeric(cutoff.closing_qty, errors="coerce").astype(float)
    if "on_order_qty" in cutoff.columns:
        cutoff["en_transito"] = pd.to_numeric(cutoff.on_order_qty, errors="coerce").astype(float)
    else:
        cutoff["en_transito"] = np.nan
    stock = cutoff[["store_id", "sku", "existencias", "en_transito", "inventario_presente"]]

    err = errors.copy()
    for col in ("n_residuos", "sesgo", "desviacion"):
        if col in err.columns:
            err[col] = pd.to_numeric(err[col], errors="coerce")

    margins = _unit_margin(prod, prices, as_of)
    prod_cols = [c for c in prod.columns if c != "unit_cost"]
    keys = forecast[["store_id", "sku"]].drop_duplicates()
    keys = keys.merge(prod[prod_cols], on="sku", how="left")
    keys = keys.merge(stock, on=["store_id", "sku"], how="left")
    err_cols = [c for c in ("store_id", "sku", "method", "n_residuos", "desviacion") if c in err.columns]
    keys = keys.merge(err[err_cols], on=["store_id", "sku"], how="left")
    keys = keys.merge(margins[["sku", "unit_cost", "unit_margin"]], on="sku", how="left")
    keys = keys.loc[keys.inventory_tracked.fillna(False)]

    weekly_mean = forecast.assign(_yhat=pd.to_numeric(forecast.yhat, errors="coerce")).groupby(["store_id", "sku"])._yhat.mean().to_dict()

    daily_map = {
        (store_id, sku): group.sort_values("date").reset_index(drop=True)
        for (store_id, sku), group in daily.groupby(["store_id", "sku"], sort=False)
    }

    candidates: list[dict[str, Any]] = []
    for row in keys.itertuples(index=False):
        notes: list[str] = []
        case_raw = row.case_size
        if case_raw is None or not np.isfinite(float(case_raw)) or float(case_raw) < 1:
            case_size = config.default_case_size
            notes.append(f"case_size por defecto={case_size}")
        else:
            case_size = int(case_raw)

        lead_days = float(row.lead_time_days) if pd.notna(row.lead_time_days) else float(config.default_lead_time_days)
        source = getattr(row, "lead_time_source", "")
        if source == "default_config":
            notes.append(f"sin lead time observado; lead_time por defecto={lead_days:g}d")
        elif source == "suppliers.standard_lead_time_days":
            notes.append("lead_time de suppliers.standard_lead_time_days")
        if row.fill_rate_source == "default_config":
            notes.append(f"fill_rate por defecto={config.default_fill_rate:g}")

        series_daily = daily_map.get((row.store_id, row.sku))
        demand_series = series_daily.daily_demand if series_daily is not None else pd.Series(dtype=float)
        valid_lead = np.isfinite(lead_days) and lead_days > 0
        lt_demand = demand_during_days(demand_series, lead_days) if valid_lead else float("nan")

        n_res = int(row.n_residuos) if pd.notna(getattr(row, "n_residuos", np.nan)) else 0
        sigma = float(row.desviacion) if pd.notna(getattr(row, "desviacion", np.nan)) else float("nan")
        sigma_source = "serie"
        if n_res < config.min_residuos or not np.isfinite(sigma):
            cat = str(row.categoria) if pd.notna(getattr(row, "categoria", pd.NA)) else "__sin_categoria__"
            sigma_cat = cat_sigma.get(cat)
            if sigma_cat is not None and np.isfinite(sigma_cat):
                sigma = float(sigma_cat) * float(weekly_mean[(row.store_id, row.sku)])
                sigma_source = "categoria_relativa"
                notes.append(f"desviacion relativa de categoría={cat} (coef={sigma_cat:.6g})")
            else:
                sigma = float(config.default_desviacion)
                sigma_source = "default_config"
                notes.append("desviacion por defecto (sin residuos de categoría)")

        # sigma_lt = desviacion × √(lead_time en semanas)
        sigma_lt = sigma * math.sqrt(lead_days / 7.0) if valid_lead else float("nan")
        safety = config.z * sigma_lt
        rop = lt_demand + safety

        cobertura_dias = float(config.cobertura_objetivo_dias)
        if bool(row.is_perishable):
            life = row.shelf_life_days
            if life is not None and np.isfinite(float(life)) and float(life) > 0:
                if cobertura_dias > float(life):
                    notes.append(f"cobertura limitada por vida útil={float(life):g}d")
                cobertura_dias = min(cobertura_dias, float(life))
            else:
                notes.append("perecedero sin shelf_life_days usable")
        cobertura_units = demand_during_days(demand_series, cobertura_dias)
        zero_demand = bool(len(demand_series)) and cobertura_units == 0 and (
            lt_demand == 0 or demand_series.eq(0).all()
        )
        if zero_demand:
            safety = 0.0
            rop = 0.0
        on_hand = float(row.existencias) + float(row.en_transito)
        inventory_known = np.isfinite(on_hand) and row.existencias >= 0 and row.en_transito >= 0
        raw_qty = max(0.0, cobertura_units + safety - on_hand) if inventory_known and valid_lead and not zero_demand else 0.0
        cantidad = _ceil_multiple(raw_qty, case_size)

        fecha_pedido = _first_order_date(series_daily, on_hand, rop, fallback_order_date)

        avg_daily = float(demand_series.mean()) if len(demand_series) else 0.0
        days_cover = on_hand / avg_daily if avg_daily > 0 else float("inf")
        if not inventory_known:
            days_cover = float("nan")
            if pd.isna(row.inventario_presente):
                notes.append(f"sin inventario al {as_of.isoformat()}")
            elif not np.isfinite(row.existencias) or row.existencias < 0:
                cause = "existencias negativas" if row.existencias < 0 else "existencias ausentes o no finitas"
                notes.append(f"{cause} al {as_of.isoformat()}")
            else:
                cause = "en tránsito negativo" if row.en_transito < 0 else "en tránsito ausente o no finito"
                notes.append(f"{cause} al {as_of.isoformat()}")
        if not valid_lead:
            notes.append(f"sin lead time válido (valor={lead_days:g})")
        weeks_cover = days_cover / 7.0
        riesgo_quiebre = _risk_stockout(days_cover, lead_days)
        riesgo_exceso = _risk_excess(weeks_cover, config.umbral_exceso_semanas)
        if zero_demand:
            riesgo_quiebre = "bajo"
            riesgo_exceso = ("alto" if row.existencias > 0 else "bajo") if np.isfinite(row.existencias) and row.existencias >= 0 else "desconocido"

        unit_cost = float(row.unit_cost) if pd.notna(getattr(row, "unit_cost", np.nan)) else float("nan")
        unit_margin = float(row.unit_margin) if pd.notna(getattr(row, "unit_margin", np.nan)) else float("nan")
        fill = float(row.fill_rate) if pd.notna(row.fill_rate) else config.default_fill_rate
        if np.isfinite(unit_cost) and unit_cost > 0 and np.isfinite(unit_margin):
            # margen bruto esperado por peso invertido (ajusta por cumplimiento)
            score = (unit_margin * fill) / unit_cost
            investment = cantidad * unit_cost
        else:
            score = float("-inf")
            investment = float("inf") if cantidad > 0 else 0.0
            if cantidad > 0:
                notes.append("sin margen/costo confiable para priorizar")

        if cantidad <= 0 and riesgo_quiebre == "bajo" and riesgo_exceso == "bajo" and not zero_demand:
            continue

        if zero_demand:
            motivo = "Sin pedido: sin demanda esperada (SS=0, cobertura infinita)"
        elif cantidad > 0:
            motivo = f"Pedir {cantidad} (ROP={rop:.1f}, SS={safety:.1f}, LT={lead_days:g}d)"
        elif riesgo_exceso == "alto":
            motivo = f"Exceso de cobertura ({weeks_cover:.1f} sem > {config.umbral_exceso_semanas:g}); no pedir"
        elif "desconocido" in (riesgo_quiebre, riesgo_exceso):
            motivo = "Sin pedido: riesgo no evaluable"
        else:
            motivo = "Sin pedido: stock proyectado cubre política"
        if notes:
            motivo = f"{motivo}; " + "; ".join(notes)

        candidates.append({
            "sku": row.sku,
            "store_id": row.store_id,
            "cantidad": int(cantidad),
            "fecha_pedido": fecha_pedido.isoformat(),
            "riesgo_quiebre": riesgo_quiebre,
            "riesgo_exceso": riesgo_exceso,
            "motivo": motivo,
            "stock_seguridad": safety,
            "fuente_desviacion": sigma_source,
            "valores_por_defecto": any("por defecto" in note for note in notes),
            "priority_score": score,
            "investment": investment if np.isfinite(investment) else float("inf"),
            "needs_order": cantidad > 0,
        })

    if not candidates:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    frame = pd.DataFrame(candidates)
    ordered = frame.sort_values(
        ["priority_score", "sku", "store_id"], ascending=[False, True, True], kind="stable",
    ).reset_index(drop=True)
    remaining = config.presupuesto
    funded_mask: list[bool] = []
    for item in ordered.itertuples(index=False):
        if remaining is None:
            funded_mask.append(True)
            continue
        if not bool(item.needs_order):
            funded_mask.append(True)
            continue
        cost = float(item.investment)
        if cost <= remaining + 1e-9:
            remaining -= max(cost, 0.0)
            funded_mask.append(True)
        else:
            funded_mask.append(False)
    ordered["funded"] = funded_mask
    excluded = ordered.loc[ordered.needs_order & ~ordered.funded].copy()
    kept = ordered.loc[ordered.funded].copy()
    if not excluded.empty:
        excluded["cantidad"] = 0
        excluded["motivo"] = excluded.motivo.map(lambda text: f"Fuera de presupuesto; {text}")
    out = pd.concat([kept, excluded], ignore_index=True)
    return out[OUTPUT_COLUMNS].sort_values(["store_id", "sku"], kind="stable").reset_index(drop=True)
