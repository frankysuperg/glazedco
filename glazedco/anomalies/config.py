"""Parámetros de detección y tratamiento, independientes del dato original."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AnomalyConfig:
    window_days: int = 56
    min_history: int = 6
    threshold: float = 6.0
    same_weekday: bool = True
    scale_floor: float = 1.0
    percent_scale_floor: float = 0.5
    forecast_window_weeks: int = 12
    forecast_min_history: int = 6
    forecast_action: str = "conservar"

    def __post_init__(self) -> None:
        if self.window_days < 1 or self.forecast_window_weeks < 1:
            raise ValueError("Las ventanas deben ser positivas")
        if self.min_history < 2 or self.forecast_min_history < 2:
            raise ValueError("Se requieren al menos dos observaciones históricas")
        if min(self.threshold, self.scale_floor, self.percent_scale_floor) <= 0:
            raise ValueError("Umbral y pisos de escala deben ser positivos")
        if self.forecast_action not in {"conservar", "excluir", "suavizar"}:
            raise ValueError("forecast_action: conservar, excluir o suavizar")


def load_config(path: str | Path | None = None) -> AnomalyConfig:
    with Path(__file__).with_name("config.toml").open("rb") as stream:
        values = tomllib.load(stream)
    if path is not None:
        with Path(path).open("rb") as stream:
            values.update(tomllib.load(stream))
    return AnomalyConfig(**values)
