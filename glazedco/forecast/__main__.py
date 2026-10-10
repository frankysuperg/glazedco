"""uv run python -m glazedco.forecast [--cache-dir data/clean]."""

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from glazedco.cleaning import clean_all, load_config
from glazedco.cleaning.io import read_tables
from glazedco.db import Database, DatabaseError
from glazedco.anomalies.config import load_config as load_anomaly_config

from .errors import series_forecast_errors
from .evaluate import evaluate_forecasts, forecast_future
from .prepare import weekly_demand
from .report import render_eval_report

NEEDED = ("sales_daily", "inventory_daily", "products", "stores", "store_closures")


def load_clean_cache(directory: Path, needed: tuple[str, ...] = NEEDED) -> dict[str, pd.DataFrame] | None:
    paths = {table: directory / f"{table}.parquet" for table in needed}
    if not all(path.is_file() for path in paths.values()):
        return None
    return {table: pd.read_parquet(path) for table, path in paths.items()}


def load_clean(*, needed: tuple[str, ...] = NEEDED, cache_dir: Path | None = None) -> tuple[dict[str, pd.DataFrame], str]:
    root = Path(__file__).resolve().parents[2]
    cached = load_clean_cache(cache_dir or root / "data" / "clean", needed)
    if cached is not None:
        return cached, "Parquet en data/clean (salida de glazedco.cleaning --cache)"
    config = load_config()
    with Database(assistant=True) as database:
        raw = read_tables(database, config)
    cleaned = clean_all(raw, config)
    return {table: cleaned[table][0] for table in needed}, "PostgreSQL vía glazedco.cleaning (SELECT)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--anomaly-config", type=Path)
    parser.add_argument("--anomaly-action", choices=["conservar", "excluir", "suavizar"])
    args = parser.parse_args()
    if args.horizon < 1 or args.splits < 2:
        parser.error("--horizon >= 1 y --splits >= 2")
    root = Path(__file__).resolve().parents[2]
    output = args.output or root / "reports" / "forecast_eval.md"
    try:
        tables, source = load_clean()
    except DatabaseError as exc:
        print(str(exc))
        return 1
    panel = weekly_demand(tables["sales_daily"], tables["inventory_daily"], tables["products"], tables["stores"], tables["store_closures"])
    anomaly_config = load_anomaly_config(args.anomaly_config)
    if args.anomaly_action:
        anomaly_config = replace(anomaly_config, forecast_action=args.anomaly_action)
    result = evaluate_forecasts(panel, horizon=args.horizon, n_splits=args.splits, anomaly_config=anomaly_config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_eval_report(panel, result, source=source) + f"\nTratamiento de anomalías en copia: {anomaly_config.forecast_action}; verdad de validación sin ajuste.\n", encoding="utf-8")
    future = forecast_future(panel, result, horizon=args.horizon)
    preview = root / "reports" / "forecast_next_weeks.csv"
    future.to_csv(preview, index=False)
    errors = series_forecast_errors(result)
    errors_path = root / "reports" / "forecast_errors.csv"
    errors.to_csv(errors_path, index=False)
    print(f"Reporte: {output}")
    print(f"Pronóstico puntual: {preview} ({len(future)} filas).")
    print(f"Residuos por serie: {errors_path} ({len(errors)} filas).")
    print(f"Ridge retenido: {result.selection.keep_ridge}; baseline: {result.selection.best_baseline}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
