"""Chequeo de conectividad y catálogo: uv run python -m glazedco.db_check."""

import argparse
from dataclasses import asdict, dataclass
import json

from .db import Database, DatabaseError


# Incluye descendientes (particiones/chunks) sin escanear filas de negocio.
# UNION evita duplicar descendientes en casos de herencia múltiple.
TABLE_ESTIMATES = """
WITH RECURSIVE relations AS (
    SELECT c.oid AS root, c.oid AS child, c.relname
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
    UNION
    SELECT r.root, i.inhrelid, r.relname
    FROM relations r
    JOIN pg_catalog.pg_inherits i ON i.inhparent = r.child
)
SELECT r.relname,
       CASE WHEN bool_or(c.reltuples < 0) FILTER (WHERE c.relkind = 'r')
            THEN NULL
            ELSE COALESCE(sum(c.reltuples) FILTER (WHERE c.relkind = 'r'), 0)::bigint
       END AS approximate_rows
FROM relations r
JOIN pg_catalog.pg_class c ON c.oid = r.child
GROUP BY r.root, r.relname
ORDER BY r.relname
"""


@dataclass(frozen=True)
class TableInfo:
    name: str
    approximate_rows: int | None
    hypertable: bool


@dataclass(frozen=True)
class CheckResult:
    select_one: int
    server_version: str
    schema: str
    connection_source: str
    assistant_source: str
    transaction_read_only: bool
    ssl: bool
    tables: list[TableInfo]


def check_database(database: Database, schema: str | None = None) -> CheckResult:
    """Solo SELECT; el esquema es un parámetro, nunca SQL interpolado."""
    with database.transaction() as connection:
        one = connection.execute("SELECT 1").fetchone()[0]
        version, current_schema, readonly = connection.execute(
            "SELECT version(), current_schema(), current_setting('transaction_read_only')"
        ).fetchone()
        selected = schema if schema is not None else current_schema
        if not selected or not connection.execute(
            "SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s", (selected,)
        ).fetchone():
            raise DatabaseError("Esquema inexistente o no seleccionado; indica --schema.")
        if readonly != "on" or not connection.pgconn.ssl_in_use:
            raise DatabaseError("La conexión no confirmó SSL y transacción de solo lectura.")
        has_timescale = connection.execute(
            "SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'timescaledb'"
        ).fetchone()
        hypertables: set[str] = set()
        if has_timescale:
            hypertables = {
                row[0] for row in connection.execute(
                    "SELECT hypertable_name FROM timescaledb_information.hypertables "
                    "WHERE hypertable_schema = %s", (selected,)
                ).fetchall()
            }
        tables = [
            TableInfo(name, estimate, name in hypertables)
            for name, estimate in connection.execute(TABLE_ESTIMATES, (selected,)).fetchall()
        ]
        return CheckResult(
            one, version, selected, database.source, database.assistant_source,
            True, True, tables,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", help="Por defecto: current_schema() de la conexión")
    parser.add_argument("--assistant", action="store_true", help="Comprueba la conexión del asistente")
    args = parser.parse_args()
    try:
        with Database(assistant=args.assistant) as database:
            print(f"Conexión: {database.source}; asistente SQL: {database.assistant_source}.")
            result = check_database(database, args.schema)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        print("Filas aproximadas de pg_class.reltuples; null = estadísticas desconocidas.")
        print("Pueden estar desactualizadas, especialmente con compresión Timescale.")
        return 0
    except DatabaseError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
