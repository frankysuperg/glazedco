"""Contexto observado; coincidencia temporal no demuestra causalidad."""

from collections import defaultdict

import pandas as pd


def add_context(alerts: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Anota solo referencias/promociones y eventos presentes en tablas limpias."""
    result = alerts.copy(deep=True)
    if result.empty:
        return result
    product_events: dict[tuple, set[str]] = defaultdict(set)
    store_events: dict[tuple, set[str]] = defaultdict(set)

    def record(day: object, store: object, sku: object, label: str) -> None:
        date = pd.Timestamp(day)
        product_events[(date, store, sku)].add(label)
        store_events[(date, store)].add(label)

    sales, promos = tables["sales_daily"], tables["promotions"]
    linked = sales.loc[sales.promo_id.notna(), ["date", "store_id", "sku", "promo_id"]].merge(
        promos[["promo_id", "start_date", "end_date"]], on="promo_id", how="inner", validate="many_to_one",
    )
    linked = linked.loc[pd.to_datetime(linked.date).between(pd.to_datetime(linked.start_date), pd.to_datetime(linked.end_date))]
    for row in linked.itertuples(index=False):
        record(row.date, row.store_id, row.sku, f"promoción registrada {row.promo_id}")
    inventory = tables["inventory_daily"]
    hours = pd.to_numeric(inventory.stockout_hours, errors="coerce")
    for row in inventory.loc[hours.gt(0) & hours.le(24), ["date", "store_id", "sku"]].itertuples(index=False):
        record(row.date, row.store_id, row.sku, "quiebre registrado (stockout_hours > 0)")
    calendar: dict[pd.Timestamp, set[str]] = defaultdict(set)
    for row in tables["calendar"].itertuples(index=False):
        labels = calendar[pd.Timestamp(row.date)]
        if pd.notna(row.is_holiday) and row.is_holiday == 1:
            name = f": {row.holiday_name}" if pd.notna(row.holiday_name) else ""
            labels.add(f"feriado registrado{name}")
        if pd.notna(row.is_school_break) and row.is_school_break == 1:
            labels.add("vacaciones escolares registradas")
    closures: dict[object, list] = defaultdict(list)
    for row in tables["store_closures"].itertuples(index=False):
        if pd.notna(row.start_date) and pd.notna(row.end_date):
            closures[row.store_id].append(row)
    causes = []
    for row in result.itertuples(index=False):
        day = pd.Timestamp(row.fecha)
        labels = set(calendar.get(day, set()))
        events = product_events.get((day, row.store_id, row.sku), set()) if row.entidad == "producto" else store_events.get((day, row.store_id), set())
        labels.update(events)
        for window in closures.get(row.store_id, []):
            if pd.Timestamp(window.start_date) <= day <= pd.Timestamp(window.end_date):
                reason = getattr(window, "reason", None)
                labels.add("cierre registrado" + (f": {reason}" if pd.notna(reason) else ""))
        causes.append("; ".join(sorted(labels)) if labels else "sin causa identificada")
    result["causa_contextual"] = causes
    return result
