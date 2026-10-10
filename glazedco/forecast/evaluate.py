"""Validación temporal de origen móvil y métricas por sucursal y total."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from .models import croston_sba, is_intermittent, moving_average, seasonal_naive
from .prepare import target_series
from .select import choose_complex_model, fit_ridge, lag_frame, predict_origin, ridge_batch

def scores(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    y = np.asarray(actual, dtype=float)
    yhat = np.asarray(predicted, dtype=float)
    mask = np.isfinite(y) & np.isfinite(yhat)
    y, yhat = y[mask], yhat[mask]
    if y.size == 0:
        return {"mae": float("nan"), "rmse": float("nan"), "wape": float("nan"), "n": 0}
    err = yhat - y
    denom = float(np.abs(y).sum())
    return {
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "wape": float(np.abs(err).sum() / denom * 100) if denom > 0 else float("nan"),
        "n": int(y.size),
    }


@dataclass
class EvalResult:
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    selection: object
    residuals: pd.Series
    histories: dict[tuple[object, object], np.ndarray] = field(default_factory=dict)
    weeks_index: dict[tuple[object, object], np.ndarray] = field(default_factory=dict)


def _metric_rows(predictions: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for model in predictions.model.unique():
        subset = predictions.loc[predictions.model.eq(model)]
        for horizon, piece in subset.groupby("horizonte"):
            rows.append({"model": model, "nivel": "sku_sucursal", "horizonte": int(horizon), **scores(piece.y, piece.yhat)})
        rows.append({"model": model, "nivel": "sku_sucursal", "horizonte": "1-4", **scores(subset.y, subset.yhat)})
        store = subset.groupby(["store_id", "semana", "horizonte"], dropna=False)[["y", "yhat"]].sum().reset_index()
        for horizon, piece in store.groupby("horizonte"):
            rows.append({"model": model, "nivel": "sucursal", "horizonte": int(horizon), **scores(piece.y, piece.yhat)})
        rows.append({"model": model, "nivel": "sucursal", "horizonte": "1-4", **scores(store.y, store.yhat)})
        total = subset.groupby(["semana", "horizonte"], dropna=False)[["y", "yhat"]].sum().reset_index()
        for horizon, piece in total.groupby("horizonte"):
            rows.append({"model": model, "nivel": "total", "horizonte": int(horizon), **scores(piece.y, piece.yhat)})
        rows.append({"model": model, "nivel": "total", "horizonte": "1-4", **scores(total.y, total.yhat)})
    return pd.DataFrame(rows)


def evaluate_forecasts(panel: pd.DataFrame, *, horizon: int = 4, n_splits: int = 4, period: int = 4, window: int = 4) -> EvalResult:
    """TimeSeriesSplit sobre semanas globales; cada origen solo ve el pasado."""
    labeled = lag_frame(panel)
    labeled["y"] = target_series(labeled)
    weeks = np.array(sorted(labeled.semana.dropna().unique()))
    if weeks.size < n_splits + horizon:
        raise ValueError("No hay semanas suficientes para la validación de origen móvil.")
    splitter = TimeSeriesSplit(n_splits=n_splits, test_size=horizon)
    grouped = {key: group.sort_values("semana") for key, group in labeled.groupby(["store_id", "sku"], sort=False, dropna=False)}
    histories = {key: target_series(group).to_numpy(dtype=float) for key, group in grouped.items()}
    week_pos = {key: group.semana.to_numpy() for key, group in grouped.items()}
    records: list[dict[str, object]] = []
    for train_idx, test_idx in splitter.split(weeks):
        origin_week = weeks[train_idx[-1]]
        test_weeks = weeks[test_idx]
        train_rows = labeled.loc[labeled.semana.le(origin_week)]
        ridge = fit_ridge(train_rows)
        origins = {}
        for key, positions in week_pos.items():
            origin = int(np.searchsorted(positions, origin_week, side="right") - 1)
            if origin >= 0:
                origins[key] = origin
        ridge_preds = ridge_batch(ridge, histories, week_pos, origins, test_weeks) if ridge is not None else {}
        for key, origin in origins.items():
            positions = week_pos[key]
            history = histories[key]
            past = history[: origin + 1]
            intermittent = is_intermittent(past)
            forecasts = {
                "seasonal_naive": seasonal_naive(past, horizon, period=period),
                "moving_average": moving_average(past, horizon, window=window),
                "sba": croston_sba(past, horizon),
            }
            if key in ridge_preds:
                forecasts["ridge"] = ridge_preds[key]
            store, sku = key
            for model, preds in forecasts.items():
                for step, future in enumerate(test_weeks):
                    idx = int(np.searchsorted(positions, future))
                    if idx >= len(positions) or pd.Timestamp(positions[idx]) != pd.Timestamp(future):
                        continue
                    actual = history[idx]
                    if not np.isfinite(actual):
                        continue
                    records.append({
                        "store_id": store, "sku": sku, "semana": pd.Timestamp(future),
                        "horizonte": step + 1, "model": model, "y": float(actual),
                        "yhat": float(preds[step]) if step < len(preds) else float("nan"),
                        "intermittent": intermittent,
                    })
    predictions = pd.DataFrame(records)
    metrics = _metric_rows(predictions) if not predictions.empty else pd.DataFrame()
    wape_total = {}
    if not metrics.empty:
        slice_ = metrics.loc[metrics.nivel.eq("total") & metrics.horizonte.astype(str).eq("1-4")]
        wape_total = {row.model: row.wape for row in slice_.itertuples()}
    selection = choose_complex_model(wape_total)
    residuals = pd.Series(dtype=float)
    if not predictions.empty:
        chosen = "ridge" if selection.keep_ridge else selection.best_baseline
        piece = predictions.loc[predictions.model.eq(chosen)]
        residuals = piece.y - piece.yhat
    return EvalResult(predictions, metrics, selection, residuals, histories, week_pos)


def residual_bands(point: pd.Series, residuals: pd.Series, low: float = 0.1, high: float = 0.9) -> pd.DataFrame:
    q_low, q_high = residuals.quantile([low, high]) if residuals.notna().any() else (0.0, 0.0)
    return pd.DataFrame({
        "yhat": point,
        "yhat_low": (point + q_low).clip(lower=0),
        "yhat_high": (point + q_high).clip(lower=0),
    })


def forecast_future(panel: pd.DataFrame, result: EvalResult, *, horizon: int = 4, period: int = 4, window: int = 4) -> pd.DataFrame:
    labeled = lag_frame(panel)
    ridge = fit_ridge(labeled) if result.selection.keep_ridge else None
    rows: list[dict[str, object]] = []
    for key, history in result.histories.items():
        weeks = result.weeks_index[key]
        if weeks.size == 0:
            continue
        origin = weeks.size - 1
        method = "sba" if is_intermittent(history) else ("ridge" if result.selection.keep_ridge else result.selection.best_baseline)
        if method == "baseline_best":
            method = result.selection.best_baseline
        preds = predict_origin(history, weeks, origin, horizon, method=method, ridge=ridge, period=period, window=window)
        last = pd.Timestamp(weeks[origin])
        store, sku = key
        for step, value in enumerate(preds, start=1):
            rows.append({
                "store_id": store, "sku": sku,
                "semana": last + pd.Timedelta(weeks=step),
                "horizonte": step, "method": method, "yhat": float(value) if np.isfinite(value) else 0.0,
            })
    future = pd.DataFrame(rows)
    if future.empty:
        return future
    bands = residual_bands(future.yhat, result.residuals)
    return pd.concat([future.reset_index(drop=True), bands.drop(columns="yhat").reset_index(drop=True)], axis=1)
