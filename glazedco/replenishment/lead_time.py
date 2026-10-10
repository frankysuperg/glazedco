"""Lead time y cumplimiento desde órdenes válidas de cleaning; respaldo de suppliers."""

from __future__ import annotations

import pandas as pd

from .config import ReplenishmentConfig


def estimate_supplier_lead_times(
    purchase_orders: pd.DataFrame,
    suppliers: pd.DataFrame,
    config: ReplenishmentConfig,
) -> pd.DataFrame:
    """Estima lead_time_days y fill_rate por supplier_id.

    Solo usa filas con `valida_plazos_cumplimiento` (órdenes recibidas válidas de
    la capa de limpieza). Si no hay muestra, cae a `standard_lead_time_days` y,
    si también falta, al default configurable (marcado fuera de esta función).
    """
    required = {"supplier_id", "order_date", "received_date", "qty_ordered", "qty_received"}
    missing = required.difference(purchase_orders.columns)
    if missing:
        raise ValueError(f"purchase_orders limpio incompleto: faltan {sorted(missing)}")

    orders = purchase_orders.copy()
    if "valida_plazos_cumplimiento" in orders.columns:
        orders = orders.loc[orders.valida_plazos_cumplimiento.eq(True)]
    orders["order_date"] = pd.to_datetime(orders.order_date, errors="coerce")
    orders["received_date"] = pd.to_datetime(orders.received_date, errors="coerce")
    orders["qty_ordered"] = pd.to_numeric(orders.qty_ordered, errors="coerce")
    orders["qty_received"] = pd.to_numeric(orders.qty_received, errors="coerce")
    orders["lead_days"] = (orders.received_date - orders.order_date).dt.days.astype("Float64")
    usable = orders.loc[
        orders.lead_days.notna()
        & orders.lead_days.ge(0)
        & orders.qty_ordered.gt(0)
        & orders.qty_received.ge(0)
    ]
    if usable.empty:
        by_supplier = pd.DataFrame(columns=["supplier_id", "lead_time_days", "fill_rate", "n_ordenes"])
    else:
        fill = usable.qty_received.clip(upper=usable.qty_ordered) / usable.qty_ordered
        by_supplier = usable.assign(fill_rate=fill).groupby("supplier_id", dropna=False).agg(
            lead_time_days=("lead_days", "median"),
            fill_rate=("fill_rate", "mean"),
            n_ordenes=("lead_days", "size"),
        ).reset_index()

    supplier_cols = ["supplier_id"]
    if "standard_lead_time_days" in suppliers.columns:
        supplier_cols.append("standard_lead_time_days")
    meta = suppliers[supplier_cols].drop_duplicates("supplier_id", keep="first")
    merged = meta.merge(by_supplier, on="supplier_id", how="left")
    merged["lead_time_source"] = "ordenes_validas"
    missing_lt = merged.lead_time_days.isna()
    if "standard_lead_time_days" in merged.columns:
        backup = pd.to_numeric(merged.standard_lead_time_days, errors="coerce")
        use_backup = missing_lt & backup.notna() & backup.gt(0)
        merged.loc[use_backup, "lead_time_days"] = backup.loc[use_backup]
        merged.loc[use_backup, "lead_time_source"] = "suppliers.standard_lead_time_days"
        missing_lt = merged.lead_time_days.isna()
    merged.loc[missing_lt, "lead_time_days"] = config.default_lead_time_days
    merged.loc[missing_lt, "lead_time_source"] = "default_config"
    merged["fill_rate"] = pd.to_numeric(merged.fill_rate, errors="coerce")
    missing_fr = merged.fill_rate.isna() | merged.fill_rate.le(0)
    merged["fill_rate_source"] = "ordenes_validas"
    merged.loc[missing_fr, "fill_rate_source"] = "default_config"
    merged.loc[missing_fr, "fill_rate"] = config.default_fill_rate
    merged["n_ordenes"] = pd.to_numeric(merged.n_ordenes, errors="coerce").fillna(0).astype(int)
    return merged[["supplier_id", "lead_time_days", "fill_rate", "n_ordenes", "lead_time_source", "fill_rate_source"]]
