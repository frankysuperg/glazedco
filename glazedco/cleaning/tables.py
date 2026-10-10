"""Reglas de maestros, inventario, precios y órdenes."""

import pandas as pd

from .config import Config
from .core import Batch, Result


def clean_products(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "products")
    b.numeric(["unit_cost", "shelf_life_days"])
    b.deduplicate(config, rank_products=True)
    mask = b.data.shelf_life_days.lt(config.nonnegative_min) & ~b.rejected
    b.data["vida_util_invalida"] = mask
    b.rule("vida_util_negativa", mask)
    b.data.loc[mask, "shelf_life_days"] = float("nan")
    return b.finish()


def clean_product_barcodes(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "product_barcodes")
    b.deduplicate(config)
    return b.finish()


def clean_stores(frame: pd.DataFrame, sales: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "stores")
    b.dates(["opened_date", "closed_date"])
    mask = ~b.data.store_id.isin(sales.store_id.dropna())
    b.rule("tienda_sin_ventas", mask, quarantine=True)
    return b.finish(tiendas_sin_ventas=b.data.loc[mask, ["store_id", "store_name"]].to_dict("records"))


def clean_purchase_orders(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "purchase_orders")
    b.deduplicate(config)
    b.numeric(["qty_ordered", "qty_received", "unit_cost"])
    b.dates(["order_date", "received_date", "expected_date"])
    d = b.data
    pending = d.status.isin(config.pending_statuses)
    received = d.status.isin(config.received_statuses)
    b.rule("orden_pendiente_o_cancelada", pending, quarantine=True)
    b.rule("estado_no_admitido", ~pending & ~received, quarantine=True)
    b.rule("recepcion_sin_fecha", received & d.received_date.isna(), quarantine=True)
    b.rule("fecha_pedido_invalida", received & d.order_date.isna(), quarantine=True)
    b.rule("recepcion_anterior_pedido", received & d.received_date.lt(d.order_date), quarantine=True)
    valid_values = d[["qty_ordered", "qty_received", "unit_cost"]].gt(config.positive_min_exclusive).all(axis=1)
    b.rule("cantidad_o_costo_no_positivo", received & ~valid_values, quarantine=True)
    b.data["valida_plazos_cumplimiento"] = ~b.rejected
    return b.finish()


def clean_inventory_daily(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "inventory_daily")
    b.numeric(["opening_qty", "received_qty", "units_sold", "expired_qty", "damaged_qty", "closing_qty", "stockout_hours"])
    b.dates(["date"])
    d = b.data
    expected = d.opening_qty + d.received_qty - d.units_sold - d.expired_qty - d.damaged_qty
    negative = d.closing_qty.lt(config.nonnegative_min)
    mismatch = d.closing_qty.sub(expected).abs().gt(config.inventory_tolerance + config.numeric_epsilon)
    hours = ~d.stockout_hours.between(config.stockout_min, config.stockout_max)
    unknown = expected.isna() | d.closing_qty.isna()
    for name, mask in (("inventario_negativo", negative), ("balance_inventario", mismatch),
                       ("horas_quiebre_invalidas", hours), ("balance_desconocido", unknown)):
        b.rule(name, mask)
    d["inventario_invalido"] = negative | mismatch | hours | unknown
    d["quiebre"] = d.stockout_hours.gt(config.stockout_min)
    b.rule("quiebre", d.quiebre)
    return b.finish()


def clean_store_daily_kpis(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "store_daily_kpis")
    b.numeric(config.kpi_hour_columns)
    b.dates(["date"])
    for column in config.kpi_hour_columns:
        mask = b.data[column].lt(config.nonnegative_min)
        b.data[f"{column}_invalido"] = mask
        b.rule(f"{column}_negativo", mask)
        b.data.loc[mask, column] = float("nan")
    return b.finish()


def clean_prices(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "prices")
    b.dates(["effective_from", "effective_to", "last_updated"])
    d = b.data
    inverted = d.effective_to.lt(d.effective_from)
    overlap = pd.Series(False, index=d.index)
    candidates = d.loc[~inverted & d.effective_from.notna()]
    for _, group in candidates.groupby(["store_id", "sku"], dropna=False, sort=False):
        active: list[tuple[pd.Timestamp, int]] = []
        for index, row in group.sort_values("effective_from", kind="stable").iterrows():
            start = row.effective_from
            if config.price_end_inclusive:
                active = [(end, i) for end, i in active if end >= start]
            else:
                active = [(end, i) for end, i in active if end > start]
            if active:
                overlap.loc[[index, *(i for _, i in active)]] = True
            active.append((row.effective_to if pd.notna(row.effective_to) else pd.Timestamp.max, index))
    d["intervalo_invertido"] = inverted
    d["intervalo_solapado"] = overlap
    b.rule("intervalo_invertido", inverted)
    b.rule("intervalo_solapado", overlap)
    return b.finish()


def clean_sites(frame: pd.DataFrame, config: Config) -> Result:
    return Batch(frame, "sites").finish()


def clean_suppliers(frame: pd.DataFrame, config: Config) -> Result:
    return Batch(frame, "suppliers").finish()


def clean_store_closures(frame: pd.DataFrame, config: Config) -> Result:
    b = Batch(frame, "store_closures")
    b.dates(["start_date", "end_date"])
    return b.finish()


def clean_promotions(frame: pd.DataFrame, config: Config) -> Result:
    return Batch(frame, "promotions").finish()


def clean_calendar(frame: pd.DataFrame, config: Config) -> Result:
    return Batch(frame, "calendar").finish()
