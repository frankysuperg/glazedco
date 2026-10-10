"""Parámetros de reabastecimiento; valores por defecto en config.toml."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class ReplenishmentConfig:
    z: float
    cobertura_objetivo_dias: float
    umbral_exceso_semanas: float
    presupuesto: float
    min_residuos: int
    as_of_date: str
    forecast_start_date: str
    default_case_size: int
    default_lead_time_days: float
    default_fill_rate: float
    default_desviacion: float
    category_column: str

    def __post_init__(self) -> None:
        if self.z < 0 or self.cobertura_objetivo_dias < 0 or self.umbral_exceso_semanas < 0:
            raise ValueError("z, cobertura_objetivo_dias y umbral_exceso_semanas deben ser >= 0")
        if self.presupuesto < 0:
            raise ValueError("presupuesto debe ser >= 0")
        if self.min_residuos < 1:
            raise ValueError("min_residuos debe ser >= 1")
        if self.default_case_size < 1:
            raise ValueError("default_case_size debe ser >= 1")
        if self.default_lead_time_days <= 0 or self.default_fill_rate <= 0:
            raise ValueError("default_lead_time_days y default_fill_rate deben ser > 0")


def load_config(path: str | Path | None = None, **overrides: Any) -> ReplenishmentConfig:
    with Path(__file__).with_name("config.toml").open("rb") as stream:
        values = tomllib.load(stream)
    if path is not None:
        with Path(path).open("rb") as stream:
            values.update(tomllib.load(stream))
    values.update(overrides)
    return ReplenishmentConfig(**values)
