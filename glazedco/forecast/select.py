"""Ridge con rezagos pasados; no se usa si no mejora los baselines."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .models import croston_sba, is_intermittent, moving_average, seasonal_naive

LAGS = (1, 2, 4)
FEATURES = ("lag1", "lag2", "lag4", "ma4", "week_sin", "week_cos")


def _week_cycle(weeks: pd.Series) -> tuple[pd.Series, pd.Series]:
    iso = pd.to_datetime(weeks).dt.isocalendar().week.astype(float)
    return np.sin(2 * np.pi * iso / 52), np.cos(2 * np.pi * iso / 52)


def lag_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """Rezagos y media móvil solo con información estrictamente anterior a cada semana."""
    frame = panel.sort_values(["store_id", "sku", "semana"], kind="stable").copy()
    keys = [frame.store_id, frame.sku]
    locf = frame.units_sold.where(frame.entrenable).groupby(keys, sort=False).ffill()
    past = locf.groupby(keys, sort=False).shift(1)
    frame["lag1"] = past
    frame["lag2"] = locf.groupby(keys, sort=False).shift(2)
    frame["lag4"] = locf.groupby(keys, sort=False).shift(4)
    frame["lag2"] = frame["lag2"].fillna(frame["lag1"])
    frame["lag4"] = frame["lag4"].fillna(frame["lag1"])
    ma4 = past.groupby(keys, sort=False).rolling(4, min_periods=1).mean()
    frame["ma4"] = ma4.droplevel(list(range(ma4.index.nlevels - 1)))
    frame["week_sin"], frame["week_cos"] = _week_cycle(frame.semana)
    return frame


def fit_ridge(train: pd.DataFrame, alpha: float = 1.0) -> Pipeline | None:
    usable = train.loc[train.entrenable].dropna(subset=["units_sold", *FEATURES])
    if len(usable) < 12:
        return None
    model = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=alpha))])
    model.fit(usable.loc[:, FEATURES].to_numpy(dtype=float), usable.units_sold.to_numpy(dtype=float))
    return model


def _features_from_history(history: np.ndarray, weeks: np.ndarray, origin: int, future_week: pd.Timestamp) -> np.ndarray | None:
    past = history[: origin + 1]
    locf = pd.Series(past).ffill()
    if locf.dropna().empty:
        return None
    filled = locf.to_numpy(dtype=float)
    lag1 = filled[-1]
    if not np.isfinite(lag1):
        return None
    lag2 = filled[-2] if filled.size >= 2 else lag1
    lag4 = filled[-4] if filled.size >= 4 else lag1
    if not np.isfinite(lag2):
        lag2 = lag1
    if not np.isfinite(lag4):
        lag4 = lag1
    ma4 = float(pd.Series(filled).iloc[-4:].mean(skipna=True))
    if not np.isfinite(ma4):
        ma4 = lag1
    sine, cosine = _week_cycle(pd.Series([future_week]))
    return np.array([lag1, lag2, lag4, ma4, float(sine.iloc[0]), float(cosine.iloc[0])], dtype=float)


def ridge_from_origin(model: Pipeline, history: np.ndarray, weeks: np.ndarray, origin: int, horizon: int, future_weeks: np.ndarray | None = None) -> np.ndarray:
    last_week = pd.Timestamp(weeks[origin])
    targets = future_weeks if future_weeks is not None else np.array([last_week + pd.Timedelta(weeks=step) for step in range(1, horizon + 1)])
    batched = ridge_batch(model, {0: history}, {0: weeks}, {0: origin}, targets)
    return batched[0]


def ridge_batch(
    model: Pipeline,
    histories: dict,
    week_pos: dict,
    origins: dict,
    future_weeks: np.ndarray,
) -> dict:
    preds = {key: np.full(len(future_weeks), np.nan) for key in origins}
    extended = {key: histories[key][: origin + 1].astype(float).copy() for key, origin in origins.items()}
    for step, future in enumerate(future_weeks):
        rows: list[np.ndarray] = []
        keys: list = []
        for key in origins:
            row = _features_from_history(extended[key], week_pos[key], extended[key].size - 1, pd.Timestamp(future))
            if row is not None:
                rows.append(row)
                keys.append(key)
        if not rows:
            continue
        yhat = np.maximum(model.predict(np.vstack(rows)), 0.0)
        for key, value in zip(keys, yhat):
            preds[key][step] = float(value)
            extended[key] = np.append(extended[key], float(value))
    return preds


def predict_origin(
    history: np.ndarray,
    weeks: np.ndarray,
    origin: int,
    horizon: int,
    *,
    method: str,
    ridge: Pipeline | None = None,
    period: int = 4,
    window: int = 4,
) -> np.ndarray:
    past = history[: origin + 1]
    if method == "sba":
        return croston_sba(past, horizon)
    if method == "seasonal_naive":
        return seasonal_naive(past, horizon, period=period)
    if method == "moving_average":
        return moving_average(past, horizon, window=window)
    if method == "ridge" and ridge is not None:
        return ridge_from_origin(ridge, history, weeks, origin, horizon)
    return moving_average(past, horizon, window=window)


def assign_methods(histories: dict[tuple[object, object], np.ndarray], keep_ridge: bool) -> dict[tuple[object, object], str]:
    assigned: dict[tuple[object, object], str] = {}
    for key, history in histories.items():
        if is_intermittent(history):
            assigned[key] = "sba"
        elif keep_ridge:
            assigned[key] = "ridge"
        else:
            assigned[key] = "baseline_best"
    return assigned


@dataclass
class Selection:
    keep_ridge: bool
    best_baseline: str
    reason: str


def choose_complex_model(wape_by_model: dict[str, float]) -> Selection:
    naive, ma = wape_by_model.get("seasonal_naive"), wape_by_model.get("moving_average")
    ridge = wape_by_model.get("ridge")
    baselines = {name: value for name, value in (("seasonal_naive", naive), ("moving_average", ma)) if value is not None and np.isfinite(value)}
    if not baselines:
        return Selection(False, "moving_average", "Sin WAPE de baselines; se conserva la media móvil.")
    best = min(baselines, key=baselines.get)
    if ridge is None or not np.isfinite(ridge) or ridge >= baselines[best]:
        return Selection(False, best, "Ridge no mejora el WAPE de validación frente a los baselines.")
    return Selection(True, best, "Ridge reduce el WAPE de validación respecto de ambos baselines.")
