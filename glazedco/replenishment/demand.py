"""Demanda diaria a partir de yhat semanal; no recalcula modelos."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd


def daily_rates_from_forecast(
    forecast: pd.DataFrame,
    *,
    as_of: date,
    forecast_start: date,
) -> pd.DataFrame:
    """Construye tasa diaria por store_id/sku/fecha.

    -  as_of+1 .. forecast_start-1: yhat de horizonte 1 / 7
    -  desde forecast_start: yhat de la semana correspondiente / 7
    Usa `yhat` y `method` tal cual; no usa bandas globales.
    """
    required = {"store_id", "sku", "semana", "horizonte", "yhat"}
    missing = required.difference(forecast.columns)
    if missing:
        raise ValueError(f"forecast_next_weeks.csv incompleto: faltan {sorted(missing)}")

    frame = forecast.copy()
    frame["semana"] = pd.to_datetime(frame.semana, errors="coerce").dt.normalize()
    frame["horizonte"] = pd.to_numeric(frame.horizonte, errors="coerce")
    frame["yhat"] = pd.to_numeric(frame.yhat, errors="coerce").fillna(0.0).clip(lower=0)
    rows: list[dict[str, object]] = []
    for (store_id, sku), piece in frame.groupby(["store_id", "sku"], sort=False, dropna=False):
        by_h = piece.sort_values("horizonte").drop_duplicates("horizonte", keep="first")
        week1 = by_h.loc[by_h.horizonte.eq(1)]
        yhat1 = float(week1.yhat.iloc[0]) if not week1.empty else 0.0
        daily_w1 = yhat1 / 7.0
        cursor = as_of + timedelta(days=1)
        while cursor < forecast_start:
            rows.append({"store_id": store_id, "sku": sku, "date": cursor, "daily_demand": daily_w1})
            cursor += timedelta(days=1)
        for row in by_h.itertuples():
            week_start = pd.Timestamp(row.semana).date()
            daily = float(row.yhat) / 7.0
            for offset in range(7):
                day = week_start + timedelta(days=offset)
                if day <= as_of:
                    continue
                rows.append({"store_id": store_id, "sku": sku, "date": day, "daily_demand": daily})
    if not rows:
        return pd.DataFrame(columns=["store_id", "sku", "date", "daily_demand"])
    out = pd.DataFrame(rows)
    return out.drop_duplicates(["store_id", "sku", "date"], keep="last").sort_values(
        ["store_id", "sku", "date"], kind="stable"
    ).reset_index(drop=True)


def demand_during_days(daily: pd.Series, n_days: float) -> float:
    """Suma demanda esperada sobre `n_days` (fracción del último día si aplica)."""
    if n_days <= 0 or daily.empty:
        return 0.0
    whole = int(math.floor(n_days))
    frac = n_days - whole
    values = daily.to_numpy(dtype=float)
    total = float(values[:whole].sum()) if whole else 0.0
    if frac > 0 and whole < values.size:
        total += frac * float(values[whole])
    elif frac > 0 and values.size:
        total += frac * float(values[-1])
    return max(total, 0.0)
