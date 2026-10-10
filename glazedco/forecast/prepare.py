"""Agrega ventas limpias a demanda semanal por producto y sucursal."""

import pandas as pd

WEEK = "W-SUN"
REQUIRED_SALES = {"store_id", "sku", "date", "units_sold", "apta_pronostico"}
REQUIRED_INVENTORY = {"store_id", "sku", "date", "closing_qty"}
REQUIRED_PRODUCTS = {"sku", "inventory_tracked"}
REQUIRED_STORES = {"store_id"}


def week_start(dates: pd.Series) -> pd.Series:
    """Lunes de la semana ISO con cierre domingo; no mezcla años al agrupar."""
    return pd.to_datetime(dates, errors="coerce").dt.to_period(WEEK).dt.start_time


def _require(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = columns.difference(frame.columns)
    if missing or "motivo" in frame.columns:
        raise ValueError(f"Se requiere {label} limpio de glazedco.cleaning; faltan {sorted(missing) or ['sin cuarentena']}.")


def _stockout(inventory: pd.DataFrame) -> pd.Series:
    if "quiebre" in inventory.columns:
        return inventory.quiebre.eq(True)
    if "stockout_hours" in inventory.columns:
        return pd.to_numeric(inventory.stockout_hours, errors="coerce").gt(0)
    raise ValueError("El inventario limpio debe incluir quiebre o stockout_hours.")


def weekly_demand(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
    products: pd.DataFrame,
    stores: pd.DataFrame | None = None,
    closures: pd.DataFrame | None = None,
    *,
    closure_end_inclusive: bool = True,
) -> pd.DataFrame:
    """Suma unidades por sku, sucursal y semana; marca censura y aptitud de entrenamiento.

    Semanas con quiebre o closing_qty = 0 (SKU rastreados) no se entrenan como demanda 0.
    SKU sin inventario no permiten observar quiebres: se documenta, no se imputa censura.
    Cierres e intervalos fuera de opened_date/closed_date tampoco son demanda 0.
    """
    _require(sales, REQUIRED_SALES, "sales_daily")
    _require(inventory, REQUIRED_INVENTORY, "inventory_daily")
    _require(products, REQUIRED_PRODUCTS, "products")
    if stores is not None:
        _require(stores, REQUIRED_STORES, "stores")
    if closures is not None and "motivo" in closures.columns:
        raise ValueError("Se requiere store_closures limpio de glazedco.cleaning.")

    fit = sales.loc[sales.apta_pronostico.eq(True), ["store_id", "sku", "date", "units_sold"]].copy()
    fit["units_sold"] = pd.to_numeric(fit.units_sold, errors="coerce")
    fit["semana"] = week_start(fit.date)
    sales_w = fit.dropna(subset=["semana"]).groupby(["store_id", "sku", "semana"], dropna=False, sort=True, observed=True)
    sales_w = sales_w.units_sold.sum(min_count=1).rename("units_sold").reset_index()

    inv = inventory[["store_id", "sku", "date", "closing_qty"]].copy()
    inv["closing_qty"] = pd.to_numeric(inv.closing_qty, errors="coerce")
    inv["quiebre"] = _stockout(inventory)
    inv["semana"] = week_start(inv.date)
    inv = inv.dropna(subset=["semana"])
    inv_w = inv.groupby(["store_id", "sku", "semana"], dropna=False, sort=True, observed=True).agg(
        inventory_days=("date", "size"),
        dias_quiebre=("quiebre", "sum"),
        dias_stock_cero=("closing_qty", lambda s: s.eq(0).sum()),
    ).reset_index()

    tracked = products[["sku", "inventory_tracked"]].drop_duplicates("sku", keep="first")
    tracked["inventory_tracked"] = pd.to_numeric(tracked.inventory_tracked, errors="coerce").fillna(0).eq(1)
    panel = sales_w.merge(inv_w, on=["store_id", "sku", "semana"], how="outer")
    panel = panel.merge(tracked, on="sku", how="left")
    panel["inventory_tracked"] = panel.inventory_tracked.eq(True)
    panel["units_sold"] = pd.to_numeric(panel.units_sold, errors="coerce").fillna(0)
    for column in ("inventory_days", "dias_quiebre", "dias_stock_cero"):
        panel[column] = pd.to_numeric(panel[column], errors="coerce").fillna(0)
    panel["censurada"] = panel.inventory_tracked & (panel.dias_quiebre.gt(0) | panel.dias_stock_cero.gt(0))
    panel["censura_desconocida"] = panel.inventory_tracked & panel.inventory_days.eq(0)
    panel["cierre"] = False
    panel["fuera_operacion"] = False
    if stores is not None and not stores.empty:
        meta = stores[["store_id"] + [c for c in ("opened_date", "closed_date") if c in stores.columns]].drop_duplicates("store_id")
        panel = panel.merge(meta, on="store_id", how="left")
        week_end = panel.semana + pd.Timedelta(days=6)
        if "opened_date" in panel.columns:
            opened = pd.to_datetime(panel.opened_date, errors="coerce")
            panel["fuera_operacion"] |= week_end.lt(opened)
        if "closed_date" in panel.columns:
            closed = pd.to_datetime(panel.closed_date, errors="coerce")
            panel["fuera_operacion"] |= panel.semana.gt(closed)
        panel = panel.drop(columns=[c for c in ("opened_date", "closed_date") if c in panel.columns])
    if closures is not None and not closures.empty:
        panel["cierre"] = _weeks_closed(panel, closures, closure_end_inclusive)
    panel["entrenable"] = (
        ~panel.censurada & ~panel.censura_desconocida & ~panel.cierre & ~panel.fuera_operacion
    )
    return panel.sort_values(["store_id", "sku", "semana"], kind="stable").reset_index(drop=True)


def _weeks_closed(panel: pd.DataFrame, closures: pd.DataFrame, inclusive: bool) -> pd.Series:
    closed = pd.Series(False, index=panel.index)
    week_end = panel.semana + pd.Timedelta(days=6)
    for window in closures.itertuples():
        start = pd.to_datetime(getattr(window, "start_date", pd.NaT), errors="coerce")
        end = pd.to_datetime(getattr(window, "end_date", pd.NaT), errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        overlap_end = week_end.ge(start) if inclusive else week_end.gt(start)
        overlap_start = panel.semana.le(end) if inclusive else panel.semana.lt(end)
        closed |= panel.store_id.eq(window.store_id) & overlap_end & overlap_start
    return closed


def target_series(panel: pd.DataFrame) -> pd.Series:
    """Demanda observada solo en semanas entrenables; el resto queda nulo (no cero)."""
    return panel.units_sold.where(panel.entrenable).astype("Float64")

