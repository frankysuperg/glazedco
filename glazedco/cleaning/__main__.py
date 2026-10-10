"""uv run python -m glazedco.cleaning [--cache] [--cost-fallback po_median]."""

import argparse
from pathlib import Path

from glazedco.db import Database, DatabaseError
from .config import load_config
from .io import read_tables, save_cache
from .pipeline import clean_all
from .report import render_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cost-fallback", choices=("none", "po_median"))
    parser.add_argument("--cache", action="store_true")
    args = parser.parse_args()
    overrides = {"cost_fallback": args.cost_fallback} if args.cost_fallback else {}
    config = load_config(args.config, **overrides)
    root = Path(__file__).resolve().parents[2]
    try:
        with Database(assistant=True) as database:
            raw = read_tables(database, config)
        print("Limpiando las 13 tablas en memoria...", flush=True)
        results = clean_all(raw, config)
        report = root / "docs" / "cleaning_report.md"
        report.write_text(render_report(raw, results, config), encoding="utf-8")
        if args.cache:
            save_cache(results, config, root / "data" / "clean")
        print(f"Reporte generado: {report}")
        return 0
    except DatabaseError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
