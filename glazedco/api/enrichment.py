"""Campos adicionales de presentación; no altera los módulos de negocio."""

from dataclasses import dataclass
from pathlib import Path
import math
import tomllib

import pandas as pd

from .models import ForecastComparison, ObservedWeek, Week


@dataclass(frozen=True)
class EnrichmentConfig:
    anomaly_medium: float = 3.0
    anomaly_high: float = 5.0
    price_days: int = 28

    def __post_init__(self):
        if (not all(math.isfinite(v) for v in (self.anomaly_medium, self.anomaly_high))
                or not 0 <= self.anomaly_medium < self.anomaly_high
                or type(self.price_days) is not int or self.price_days < 1):
            raise ValueError("Configuración de enriquecimiento inválida")


def load_config() -> EnrichmentConfig:
    with Path(__file__).with_name("enrichment.toml").open("rb") as stream:
        return EnrichmentConfig(**tomllib.load(stream))


def finite(value: object) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def comparison(sales: pd.DataFrame, weeks: list[Week], cutoff: str, forecast_start: str) -> ForecastComparison:
    cut = pd.Timestamp(cutoff).normalize()
    monday = cut - pd.Timedelta(days=cut.weekday())
    # Una semana que termina el día del corte ya está completa.
    end = monday + pd.Timedelta(days=6) if cut.weekday() == 6 else monday - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=27)
    dates = pd.to_datetime(sales.date, errors="coerce").dt.normalize()
    observed = []
    for first in pd.date_range(start, periods=4, freq="7D"):
        last = first + pd.Timedelta(days=6)
        piece = sales.loc[dates.between(first, last)]
        units = pd.to_numeric(piece.units_sold, errors="coerce")
        valid = len(units) > 0 and units.map(lambda v: finite(v) is not None and v >= 0).all()
        observed.append(ObservedWeek(semana=first.date(), fin=last.date(),
                                     unidades=float(units.sum()) if valid else None,
                                     registros=len(piece), dias_con_registros=dates.loc[piece.index].nunique()))
    total = sum(w.unidades for w in observed) if all(w.unidades is not None for w in observed) else None
    expected = [day.date() for day in pd.date_range(forecast_start, periods=4, freq="7D")]
    by_date = {week.semana: week.unidades for week in weeks}
    projected = sum(by_date[day] for day in expected) if all(by_date.get(day) is not None for day in expected) else None
    variation = (projected - total) / total * 100 if total is not None and total != 0 and projected is not None else None
    reason = ("Faltan unidades observadas en alguna semana." if total is None else
              "El denominador observado es cero." if total == 0 else
              "No hay cuatro semanas pronosticadas completas." if projected is None else None)
    return ForecastComparison(fecha_corte=cut.date(), semanas_anteriores=observed,
                              unidades_reales_4_semanas=total, unidades_pronosticadas_4_semanas=projected,
                              variacion_porcentual=variation, motivo=reason)


def enrich_anomalies(frame: pd.DataFrame, config: EnrichmentConfig) -> pd.DataFrame:
    result = frame.copy().reset_index(drop=True)
    def severity(value):
        score = finite(value)
        return None if score is None else "alta" if abs(score) >= config.anomaly_high else "media" if abs(score) >= config.anomaly_medium else "baja"
    result["severidad"] = result.puntaje.map(severity)
    result["motivo_severidad"] = result.severidad.map(lambda v: "Puntaje ausente o no finito." if pd.isna(v) else None)
    result["duracion_dias"] = pd.Series([None] * len(result), dtype="Int64")
    dates = pd.to_datetime(result.fecha, errors="coerce").dt.normalize()
    for _, group in result.groupby(["store_id", "sku", "entidad", "metrica", "tipo"], dropna=False, sort=False):
        days = dates.loc[group.index].dropna().drop_duplicates().sort_values()
        runs = days.diff().ne(pd.Timedelta(days=1)).cumsum()
        lengths = days.groupby(runs).transform("size")
        lookup = dict(zip(days, lengths))
        result.loc[group.index, "duracion_dias"] = dates.loc[group.index].map(lookup).astype("Int64")
    return result
