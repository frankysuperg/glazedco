import numpy as np
import pandas as pd
import pytest

from glazedco.forecast.evaluate import evaluate_forecasts, residual_bands, scores
from glazedco.forecast.models import croston_sba, is_intermittent, moving_average, seasonal_naive
from glazedco.forecast.prepare import weekly_demand
from glazedco.forecast.select import choose_complex_model


def _sales(rows: list[dict]) -> pd.DataFrame:
    base = {"store_id": "S1", "sku": "A", "apta_pronostico": True}
    return pd.DataFrame([{**base, **row} for row in rows])


def _inventory(rows: list[dict]) -> pd.DataFrame:
    base = {"store_id": "S1", "sku": "A", "date": "2026-01-05", "closing_qty": 4, "quiebre": False, "stockout_hours": 0.0}
    if not rows:
        return pd.DataFrame(columns=list(base))
    return pd.DataFrame([{**base, **row} for row in rows])


def _products(tracked: int = 1) -> pd.DataFrame:
    return pd.DataFrame([{"sku": "A", "inventory_tracked": tracked}])


def test_weekly_sum_and_imputed_dates_excluded() -> None:
    sales = _sales([
        {"date": "2026-01-05", "units_sold": 2},
        {"date": "2026-01-06", "units_sold": 3},
        {"date": "2026-01-07", "units_sold": 9, "apta_pronostico": False},
        {"date": "2026-01-12", "units_sold": 1},
    ])
    inventory = _inventory([
        {"date": "2026-01-05"}, {"date": "2026-01-06"}, {"date": "2026-01-12"},
    ])
    panel = weekly_demand(sales, inventory, _products())
    week = panel.loc[panel.semana.eq(pd.Timestamp("2026-01-05"))].iloc[0]
    assert week.units_sold == 5
    assert panel.units_sold.sum() == 6


def test_stockout_week_is_censored_not_trainable_zero() -> None:
    sales = _sales([
        {"date": "2026-01-05", "units_sold": 10},
        {"date": "2026-01-12", "units_sold": 0},
        {"date": "2026-01-19", "units_sold": 10},
    ])
    inventory = _inventory([
        {"date": "2026-01-05", "closing_qty": 3, "quiebre": False},
        {"date": "2026-01-12", "closing_qty": 0, "quiebre": True, "stockout_hours": 8},
        {"date": "2026-01-19", "closing_qty": 2, "quiebre": False},
    ])
    panel = weekly_demand(sales, inventory, _products())
    stockout = panel.loc[panel.semana.eq(pd.Timestamp("2026-01-12"))].iloc[0]
    assert stockout.censurada and not stockout.entrenable
    history = panel.units_sold.where(panel.entrenable).to_numpy(dtype=float)
    assert np.isnan(history[1])
    assert moving_average(history[:2], 1)[0] == 10


def test_untracked_sku_does_not_invent_stockout() -> None:
    sales = _sales([{"date": "2026-01-05", "units_sold": 1}])
    inventory = _inventory([])
    window = (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31"))
    panel = weekly_demand(sales, inventory, _products(tracked=0), observation_window=window)
    assert not panel.censurada.any() and not panel.censura_desconocida.any()
    assert panel.entrenable.all()


def test_tracked_sale_without_inventory_is_unknown_censoring() -> None:
    sales = _sales([{"date": "2026-01-05", "units_sold": 4}])
    panel = weekly_demand(sales, _inventory([]), _products(tracked=1))
    assert panel.censura_desconocida.iloc[0] and not panel.entrenable.iloc[0]


def test_closure_and_pre_opening_are_not_demand_zero() -> None:
    sales = _sales([{"date": "2026-01-05", "units_sold": 2}, {"date": "2026-02-02", "units_sold": 2}])
    inventory = _inventory([{"date": "2026-01-05"}, {"date": "2026-02-02"}])
    stores = pd.DataFrame([{"store_id": "S1", "opened_date": "2026-01-01", "closed_date": None}])
    closures = pd.DataFrame([{"store_id": "S1", "start_date": "2026-02-01", "end_date": "2026-02-08"}])
    panel = weekly_demand(sales, inventory, _products(), stores, closures)
    closed = panel.loc[panel.semana.eq(pd.Timestamp("2026-02-02"))].iloc[0]
    assert closed.cierre and not closed.entrenable


def test_quarantine_rejected() -> None:
    with pytest.raises(ValueError, match="limpio"):
        weekly_demand(_sales([{"date": "2026-01-05", "units_sold": 1, "motivo": "x"}]), _inventory([{"date": "2026-01-05"}]), _products())


def test_seasonal_naive_matches_period_on_synthetic_seasonal() -> None:
    pattern = np.array([10.0, 20.0, 30.0, 40.0] * 8)
    preds = seasonal_naive(pattern, 4, period=4)
    assert np.allclose(preds, [10, 20, 30, 40])
    ma = moving_average(pattern, 4, window=4)
    assert ma[0] == pytest.approx(25)


def test_sba_on_intermittent_and_ignores_censored() -> None:
    zeros = np.array([0.0, 0.0, 8.0, 0.0, 0.0, 8.0, 0.0, np.nan, 0.0, 8.0])
    assert is_intermittent(zeros)
    pred = croston_sba(zeros, 2)
    assert pred[0] == pred[1] > 0
    assert pred[0] < 8


def test_wape_null_when_actuals_are_zero() -> None:
    result = scores(np.zeros(3), np.array([1.0, 2.0, 3.0]))
    assert pd.isna(result["wape"]) and result["mae"] == 2
    empty = scores(np.array([np.nan]), np.array([1.0]))
    assert empty["n"] == 0 and pd.isna(empty["mae"])


def test_lags_use_only_past_and_skip_censored_zero() -> None:
    from glazedco.forecast.select import lag_frame
    panel = pd.DataFrame({
        "store_id": ["S1"] * 5, "sku": ["A"] * 5,
        "semana": pd.date_range("2026-01-05", periods=5, freq="W-MON"),
        "units_sold": [10.0, 10.0, 0.0, 10.0, 12.0],
        "entrenable": [True, True, False, True, True],
    })
    lagged = lag_frame(panel)
    row = lagged.iloc[3]
    assert row.lag1 == 10 and row.lag2 == 10
    assert row.lag1 != 0
    assert lagged.iloc[4].lag1 == 10


def test_ridge_kept_only_if_better_than_baselines() -> None:
    keep = choose_complex_model({"seasonal_naive": 12.0, "moving_average": 11.0, "ridge": 9.0})
    drop = choose_complex_model({"seasonal_naive": 8.0, "moving_average": 11.0, "ridge": 9.0})
    assert keep.keep_ridge and keep.best_baseline == "moving_average"
    assert not drop.keep_ridge and drop.best_baseline == "seasonal_naive"


def test_rolling_origin_seasonal_beats_ma_and_excludes_stockout() -> None:
    weeks = pd.date_range("2025-10-06", periods=28, freq="W-MON")
    seasonal = np.array([10.0, 20.0, 30.0, 40.0] * 7)
    panel = pd.DataFrame({
        "store_id": ["S1"] * 28, "sku": ["A"] * 28, "semana": weeks,
        "units_sold": seasonal, "entrenable": [True] * 28,
        "inventory_tracked": True, "censurada": False, "censura_desconocida": False,
        "cierre": False, "fuera_operacion": False, "inventory_days": 7,
        "dias_quiebre": 0, "dias_stock_cero": 0,
    })
    result = evaluate_forecasts(panel, horizon=4, n_splits=3)
    wape = result.metrics.set_index(["model", "nivel", "horizonte"]).wape
    assert wape.loc["seasonal_naive", "total", "1-4"] < wape.loc["moving_average", "total", "1-4"]
    broken = panel.copy()
    broken.loc[20, ["units_sold", "entrenable", "censurada"]] = [0.0, False, True]
    censored = evaluate_forecasts(broken, horizon=4, n_splits=3)
    assert not ((censored.predictions.semana.eq(weeks[20])) & (censored.predictions.y.eq(0))).any()


def test_residual_bands_clip_at_zero() -> None:
    bands = residual_bands(pd.Series([1.0, 3.0]), pd.Series([-5.0, -1.0, 2.0]))
    assert (bands.yhat_low >= 0).all()
    assert bands.yhat_high.iloc[1] >= bands.yhat.iloc[1]

def test_partial_edge_weeks_are_not_trainable() -> None:
    dates = pd.date_range("2025-10-01", "2025-10-21")  # miércoles a martes
    sales = pd.DataFrame({"store_id": "S1", "sku": "A", "date": dates, "units_sold": 1, "apta_pronostico": True})
    inventory = pd.DataFrame({"store_id": "S1", "sku": "A", "date": dates, "closing_qty": 5, "stockout_hours": 0.0})
    products = pd.DataFrame({"sku": ["A"], "inventory_tracked": [1]})
    panel = weekly_demand(sales, inventory, products).set_index("semana")
    assert panel.parcial.tolist() == [True, False, False, True]
    assert panel.entrenable.tolist() == [False, True, True, False]