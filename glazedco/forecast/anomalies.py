"""Tratamiento opcional del histórico semanal en una copia causal."""

import pandas as pd

from glazedco.anomalies.config import AnomalyConfig
from glazedco.anomalies.robust import robust_scores


def adjust_history(panel: pd.DataFrame, config: AnomalyConfig) -> pd.DataFrame:
    """Conservar, excluir o reemplazar por mediana previa solo semanas entrenables.

    Opera en unidades semanales (la frecuencia del modelo), con el mismo
    detector robusto. No usa alertas de margen para modificar demanda ni
    rehabilita semanas censuradas. Los valores originales sirven de verdad
    de validación aunque el histórico de entrenamiento se ajuste.
    """
    result = panel.copy(deep=True)
    if config.forecast_action == "conservar":
        return result
    for _, group in result.groupby(["store_id", "sku"], sort=False):
        ordered = group.sort_values("semana")
        values = pd.Series(ordered.units_sold.where(ordered.entrenable).to_numpy(dtype=float),
                           index=pd.DatetimeIndex(ordered.semana))
        scored = robust_scores(values, window_days=config.forecast_window_weeks * 7,
                               min_history=config.forecast_min_history, threshold=config.threshold,
                               scale_floor=config.scale_floor)
        mask = scored.anomalia.to_numpy()
        indexes = ordered.index[mask]
        if config.forecast_action == "excluir":
            result.loc[indexes, "entrenable"] = False
        else:
            result["units_sold"] = result.units_sold.astype(float)
            result.loc[indexes, "units_sold"] = scored.loc[scored.anomalia, "valor_esperado"].to_numpy()
    return result
