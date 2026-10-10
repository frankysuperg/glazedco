"""uv run python -m glazedco.metrics [--limit 10]."""

import argparse

from glazedco.cleaning import clean_all, load_config
from glazedco.cleaning.io import read_tables
from glazedco.db import Database, DatabaseError
from . import LEVELS, margin_by_level


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="Máximo de grupos mostrados por nivel")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit debe ser positivo")
    config = load_config()
    try:
        with Database(assistant=True) as database:
            raw = read_tables(database, config)
        clean_sales = clean_all(raw, config)["sales_daily"][0]
        del raw
        print("Ingresos = net_sales (descuento incluido); porcentaje sobre filas con costo válido. USD.")
        for level in LEVELS:
            result = margin_by_level(clean_sales, level)
            print(f"\n{level}: {len(result)} grupos; filas excluidas: {int(result.filas_excluidas.sum())}.")
            print(result.head(args.limit).to_string(index=False, na_rep="nulo", float_format=lambda value: f"{value:.2f}"))
            if len(result) > args.limit:
                print(f"Mostrados los primeros {args.limit} grupos; aumenta --limit para ver más.")
        return 0
    except DatabaseError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
