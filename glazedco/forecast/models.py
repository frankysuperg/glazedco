"""Baselines y Croston/SBA; historia con NaN = semana no entrenable."""

import numpy as np

INTERMITTENT_ADI = 1.32
CROSTON_ALPHA = 0.1


def finite(history: np.ndarray) -> np.ndarray:
    values = np.asarray(history, dtype=float)
    return values[np.isfinite(values)]


def adi(history: np.ndarray) -> float:
    observed = finite(history)
    if observed.size == 0:
        return float("inf")
    nonzero = np.count_nonzero(observed > 0)
    return float(observed.size / nonzero) if nonzero else float("inf")


def is_intermittent(history: np.ndarray, threshold: float = INTERMITTENT_ADI) -> bool:
    return adi(history) >= threshold


def seasonal_naive(history: np.ndarray, horizon: int, period: int = 4) -> np.ndarray:
    """ŷ_{T+h} = y_{T+h-m}; si falta, último valor entrenable (no un censurado)."""
    y = np.asarray(history, dtype=float)
    observed = finite(y)
    fallback = float(observed[-1]) if observed.size else 0.0
    preds = np.empty(horizon, dtype=float)
    last = len(y) - 1
    for step in range(1, horizon + 1):
        index = last + step - period
        preds[step - 1] = float(y[index]) if 0 <= index < len(y) and np.isfinite(y[index]) else fallback
    return preds


def moving_average(history: np.ndarray, horizon: int, window: int = 4) -> np.ndarray:
    observed = finite(history)
    if observed.size == 0:
        return np.zeros(horizon)
    mean = float(observed[-window:].mean())
    return np.full(horizon, mean)


def croston_sba(history: np.ndarray, horizon: int, alpha: float = CROSTON_ALPHA, sba: bool = True) -> np.ndarray:
    """Suaviza tamaño e intervalo solo en semanas entrenables; SBA corrige sesgo de Croston."""
    demand = 0.0
    interval = 1.0
    since = 0
    started = False
    for value in np.asarray(history, dtype=float):
        if not np.isfinite(value):
            continue
        since += 1
        if value <= 0:
            continue
        if not started:
            demand, interval, started = float(value), float(since), True
        else:
            demand = alpha * float(value) + (1 - alpha) * demand
            interval = alpha * float(since) + (1 - alpha) * interval
        since = 0
    if not started:
        return np.zeros(horizon)
    rate = demand / interval if interval else 0.0
    if sba:
        rate *= 1 - alpha / 2
    return np.full(horizon, max(rate, 0.0))
