"""Alertas diarias por producto/sucursal y por sucursal sobre ventas limpias."""

import pandas as pd

from glazedco.metrics import margin_rows
from .config import AnomalyConfig
from .robust import robust_scores

METRICS = {"units_sold": "unidades", "ingresos_netos_total": "USD",
           "margen_bruto_monto": "USD", "margen_bruto_porcentaje": "%"}
COLUMNS = ["fecha", "entidad", "store_id", "sku", "metrica", "unidad", "valor_observado",
           "valor_esperado", "puntaje", "tipo", "cobertura_costo_pct", "causa_contextual"]


def daily_metrics(clean_sales: pd.DataFrame) -> pd.DataFrame:
    """Margen sobre costos conocidos; porcentaje=razón de sumas, sin doble descuento.

    Fechas imputadas se excluyen del análisis temporal. No se crean días sin
    venta ni se interpretan como demanda cero. La cobertura de costo acompaña
    las alertas: el monto es parcial si faltan costos, nunca se rellenan con 0.
    """
    if "apta_pronostico" not in clean_sales:
        raise ValueError("Se requiere sales_daily limpio con apta_pronostico")
    rows = margin_rows(clean_sales.loc[clean_sales.apta_pronostico.eq(True)])
    rows["fecha"] = pd.to_datetime(rows.date)
    rows["units_sold"] = pd.to_numeric(rows.units_sold, errors="coerce")
    frames = []
    for entity, keys in (("producto", ["store_id", "sku", "fecha"]), ("sucursal", ["store_id", "fecha"])):
        grouped = rows.groupby(keys, observed=True, sort=True)
        frame = grouped[["units_sold", "ingresos_netos_total", "ingresos_base_margen", "margen_bruto_monto"]].sum(min_count=1).reset_index()
        frame["margen_bruto_porcentaje"] = frame.margen_bruto_monto.div(frame.ingresos_base_margen.where(frame.ingresos_base_margen.ne(0))) * 100
        frame["cobertura_costo_pct"] = frame.ingresos_base_margen.div(frame.ingresos_netos_total.where(frame.ingresos_netos_total.ne(0))) * 100
        frame["entidad"] = entity
        if "sku" not in frame:
            frame["sku"] = pd.NA
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def detect_anomalies(clean_sales: pd.DataFrame, config: AnomalyConfig) -> pd.DataFrame:
    """Devuelve solo alertas, con contexto pendiente; nunca muta la entrada."""
    daily = daily_metrics(clean_sales)
    daily["_weekday"] = daily.fecha.dt.dayofweek if config.same_weekday else 0
    alerts = []
    for _, group in daily.groupby(["entidad", "store_id", "sku", "_weekday"], dropna=False, sort=False):
        group = group.sort_values("fecha").set_index("fecha")
        for metric, unit in METRICS.items():
            scored = robust_scores(group[metric], window_days=config.window_days,
                                   min_history=config.min_history, threshold=config.threshold,
                                   scale_floor=config.percent_scale_floor if unit == "%" else config.scale_floor)
            selected = scored.loc[scored.anomalia].drop(columns="anomalia")
            if selected.empty:
                continue
            for column in ("entidad", "store_id", "sku", "cobertura_costo_pct"):
                selected[column] = group.loc[selected.index, column]
            selected["metrica"], selected["unidad"] = metric, unit
            selected["tipo"] = selected.puntaje.gt(0).map({True: "pico", False: "caída"}) if not metric.startswith("margen_") else "cambio de margen"
            selected["causa_contextual"] = "sin causa identificada"
            alerts.append(selected.reset_index())
    if not alerts:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(alerts, ignore_index=True)[COLUMNS].sort_values(["fecha", "entidad", "store_id", "metrica"], kind="stable").reset_index(drop=True)
