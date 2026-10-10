"""Opciones compartidas; los valores predeterminados viven en config.toml."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class Config:
    money_tolerance: float
    numeric_epsilon: float
    inventory_tolerance: float
    nonnegative_min: float
    positive_min_exclusive: float
    stockout_min: float
    stockout_max: float
    cost_fallback: str
    fallback_same_day: bool
    closure_end_inclusive: bool
    price_end_inclusive: bool
    store_activity_source: str
    quarantine_null_keys: bool
    quarantine_missing_sale_amounts: bool
    fetch_size: int
    received_statuses: list[str]
    pending_statuses: list[str]
    sales_amount_columns: list[str]
    kpi_hour_columns: list[str]
    keys: dict[str, list[str]]

    def __post_init__(self) -> None:
        if self.cost_fallback not in {"none", "po_median"}:
            raise ValueError("cost_fallback debe ser none o po_median")
        if self.store_activity_source != "raw_sales":
            raise ValueError("store_activity_source debe ser raw_sales")
        if min(self.money_tolerance, self.numeric_epsilon, self.inventory_tolerance) < 0:
            raise ValueError("Las tolerancias deben ser no negativas")
        if self.stockout_min > self.stockout_max or self.fetch_size < 1:
            raise ValueError("Rango de stockout o fetch_size inválido")


def load_config(path: str | Path | None = None, **overrides: Any) -> Config:
    with Path(__file__).with_name("config.toml").open("rb") as stream:
        values = tomllib.load(stream)
    if path is not None:
        with Path(path).open("rb") as stream:
            custom = tomllib.load(stream)
        if "keys" in custom:
            custom["keys"] = {**values["keys"], **custom["keys"]}
        values.update(custom)
    values.update(overrides)
    return Config(**values)
