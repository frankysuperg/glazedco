"""Residuos de validación por serie; no recalcula modelos ni cambia el origen móvil."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .evaluate import EvalResult
from .models import is_intermittent


def method_label(history: np.ndarray, *, keep_ridge: bool, best_baseline: str) -> str:
    """Misma etiqueta que `forecast_future` escribe en forecast_next_weeks.csv."""
    if is_intermittent(history):
        return "sba"
    if keep_ridge:
        return "ridge"
    return best_baseline


def series_forecast_errors(result: EvalResult) -> pd.DataFrame:
    """Agrega sesgo y desviación de residuos (y − ŷ) por store_id/sku/método.

    Usa solo las filas de validación del método asignado a cada serie; no altera
    `evaluate_forecasts` ni las bandas globales del CSV de pronóstico.
    """
    rows: list[dict[str, object]] = []
    predictions = result.predictions
    keep_ridge = bool(result.selection.keep_ridge)
    best_baseline = str(result.selection.best_baseline)
    for key, history in result.histories.items():
        store_id, sku = key
        method = method_label(history, keep_ridge=keep_ridge, best_baseline=best_baseline)
        if predictions.empty:
            resid = pd.Series(dtype=float)
        else:
            piece = predictions.loc[
                predictions.store_id.eq(store_id)
                & predictions.sku.eq(sku)
                & predictions.model.eq(method)
            ]
            resid = pd.to_numeric(piece.y, errors="coerce") - pd.to_numeric(piece.yhat, errors="coerce")
            resid = resid.replace([np.inf, -np.inf], np.nan).dropna()
        n = int(resid.size)
        sesgo = float(resid.mean()) if n else float("nan")
        if n >= 2:
            desviacion = float(resid.std(ddof=1))
        elif n == 1:
            desviacion = 0.0
        else:
            desviacion = float("nan")
        rows.append({
            "store_id": store_id,
            "sku": sku,
            "method": method,
            "n_residuos": n,
            "sesgo": sesgo,
            "desviacion": desviacion,
        })
    columns = ["store_id", "sku", "method", "n_residuos", "sesgo", "desviacion"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["store_id", "sku"], kind="stable"
    ).reset_index(drop=True)
