"""uv run python -m glazedco.replenishment [--cache-dir data/clean]."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from glazedco.cleaning import clean_all, load_config as load_cleaning_config
from glazedco.cleaning.io import read_tables
from glazedco.db import Database, DatabaseError

from .config import load_config
from .recommend import OUTPUT_COLUMNS, recommend_replenishment

NEEDED = (
    "inventory_daily", "products", "purchase_orders", "suppliers", "prices",
)


def load_clean_cache(directory: Path) -> dict[str, pd.DataFrame] | None:
    paths = {table: directory / f"{table}.parquet" for table in NEEDED}
    if not all(path.is_file() for path in paths.values()):
        return None
    return {table: pd.read_parquet(path) for table, path in paths.items()}


def load_clean(cache_dir: Path | None) -> tuple[dict[str, pd.DataFrame], str]:
    root = Path(__file__).resolve().parents[2]
    directory = cache_dir or (root / "data" / "clean")
    cached = load_clean_cache(directory)
    if cached is not None:
        return cached, f"Parquet en {directory}"
    config = load_cleaning_config()
    with Database(assistant=True) as database:
        raw = read_tables(database, config)
    cleaned = clean_all(raw, config)
    return {table: cleaned[table][0] for table in NEEDED}, "PostgreSQL vía glazedco.cleaning (SELECT)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--forecast", type=Path)
    parser.add_argument("--errors", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    config = load_config(args.config)
    forecast_path = args.forecast or root / "reports" / "forecast_next_weeks.csv"
    errors_path = args.errors or root / "reports" / "forecast_errors.csv"
    output = args.output or root / "reports" / "replenishment_recommendations.csv"
    if not forecast_path.is_file():
        print(f"Falta {forecast_path}; ejecuta primero `uv run python -m glazedco.forecast`.")
        return 1
    if not errors_path.is_file():
        print(f"Falta {errors_path}; ejecuta primero `uv run python -m glazedco.forecast`.")
        return 1
    try:
        tables, source = load_clean(args.cache_dir)
    except DatabaseError as exc:
        print(str(exc))
        return 1
    forecast = pd.read_csv(forecast_path)
    errors = pd.read_csv(errors_path)
    result = recommend_replenishment(
        forecast=forecast,
        errors=errors,
        inventory=tables["inventory_daily"],
        products=tables["products"],
        purchase_orders=tables["purchase_orders"],
        suppliers=tables["suppliers"],
        config=config,
        prices=tables.get("prices"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    print(f"Fuente maestros/inventario: {source}")
    print(f"Recomendaciones: {output} ({len(result)} filas).")
    print(f"Columnas: {', '.join(OUTPUT_COLUMNS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
