"""Limpieza de ventas; enriquece costos sin calcular margen ni pronósticos."""

import pandas as pd

from .config import Config
from .core import Batch, Result


def historical_costs(sales: pd.DataFrame, orders: pd.DataFrame, config: Config) -> pd.Series:
    """Mediana por SKU de órdenes limpias recibidas antes de cada venta."""
    result = pd.Series(float("nan"), index=sales.index)
    if orders.empty or sales.empty:
        return result
    history = orders.loc[orders.sku.notna() & orders.received_date.notna(), ["sku", "received_date", "unit_cost"]].copy()
    if history.empty:
        return result
    history = history.sort_values(["sku", "received_date"], kind="stable").reset_index(drop=True)
    history["_median"] = history.groupby("sku", sort=False).unit_cost.expanding().median().reset_index(level=0, drop=True)
    history = history.drop_duplicates(["sku", "received_date"], keep="last").sort_values("received_date")
    candidates = sales.loc[sales.sku.notna() & sales.date.notna(), ["sku", "date", "_source_row"]].sort_values("date")
    if candidates.empty:
        return result
    matched = pd.merge_asof(
        candidates, history, by="sku", left_on="date", right_on="received_date",
        direction="backward", allow_exact_matches=config.fallback_same_day,
    )
    result.loc[matched._source_row] = matched._median.to_numpy()
    return result


def clean_sales_daily(
    frame: pd.DataFrame, products: pd.DataFrame, stores: pd.DataFrame,
    closures: pd.DataFrame, promotions: pd.DataFrame, orders: pd.DataFrame,
    config: Config,
) -> Result:
    """Los maestros y órdenes deben ser las salidas limpias de sus funciones."""
    b = Batch(frame, "sales_daily")
    b.dates(["date"])
    b.deduplicate(config)
    b.numeric(["units_sold", *config.sales_amount_columns])
    d = b.data
    b.rule("unidades_nulas", d.units_sold.isna(), quarantine=True)
    b.rule("valores_negativos", d[["units_sold", *config.sales_amount_columns]].lt(config.nonnegative_min).any(axis=1), quarantine=True)
    if config.quarantine_missing_sale_amounts:
        b.rule("importe_nulo", d[config.sales_amount_columns].isna().any(axis=1), quarantine=True)
    b.rule("fecha_invalida", d.date.isna(), quarantine=True)
    tolerance = config.money_tolerance + config.numeric_epsilon
    b.rule("net_sales_inconsistente", d.net_sales.sub(d.gross_sales - d.discount_amount).abs().gt(tolerance), quarantine=True)
    b.rule("gross_sales_inconsistente", d.gross_sales.sub(d.units_sold * d.unit_price).abs().gt(tolerance), quarantine=True)
    b.rule("sku_inexistente", ~d.sku.isin(products.sku.dropna()), quarantine=True)
    b.rule("store_id_inexistente", ~d.store_id.isin(stores.store_id.dropna()), quarantine=True)
    # validate impide reutilizar maestros duplicados e inflar ingresos.
    d = d.merge(products[["sku", "unit_cost"]].rename(columns={"unit_cost": "unit_cost_aplicado"}),
                on="sku", how="left", validate="many_to_one", sort=False)
    d = d.merge(stores[["store_id", "opened_date"]], on="store_id", how="left", validate="many_to_one", sort=False)
    d = d.sort_values("_source_row").reset_index(drop=True)
    b.data = d
    b.rule("venta_antes_apertura", d.date.lt(pd.to_datetime(d.opened_date)), quarantine=True)
    closed = pd.Series(False, index=d.index)
    for window in closures.itertuples():
        if pd.isna(window.start_date) or pd.isna(window.end_date):
            continue
        end = d.date.le(window.end_date) if config.closure_end_inclusive else d.date.lt(window.end_date)
        closed |= d.store_id.eq(window.store_id) & d.date.ge(window.start_date) & end
    b.rule("venta_durante_cierre", closed, quarantine=True)
    retained = ~b.rejected
    unknown_promo = retained & d.promo_id.notna() & ~d.promo_id.isin(promotions.promo_id.dropna())
    d["promo_inexistente"] = unknown_promo
    b.rule("promo_inexistente", unknown_promo)
    d.loc[unknown_promo, "promo_id"] = pd.NA
    d["fecha_imputada"] = d.imputado.notna()
    d["apta_pronostico"] = ~d.fecha_imputada
    b.rule("fecha_imputada", retained & d.fecha_imputada)
    d["costo_estimado"] = False
    if config.cost_fallback == "po_median":
        fallback = historical_costs(d, orders, config)
        estimated = retained & d.unit_cost_aplicado.isna() & fallback.notna()
        d.loc[estimated, "unit_cost_aplicado"] = fallback.loc[estimated]
        d.loc[estimated, "costo_estimado"] = True
    d["costo_desconocido"] = d.unit_cost_aplicado.isna()
    d["incluible_margen"] = ~d.costo_desconocido
    b.rule("costo_desconocido", retained & d.costo_desconocido)
    b.rule("costo_estimado", retained & d.costo_estimado)
    return b.finish()
