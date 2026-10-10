"""Lectura SELECT acotada al catálogo principal y caché local opcional."""

from dataclasses import asdict
import json
from pathlib import Path
import pandas as pd
from psycopg import sql

from glazedco.db import Database
from .config import Config
from .core import Result
from .pipeline import TABLES


def read_tables(database: Database, config: Config) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    with database.transaction() as connection:
        for table in TABLES:
            # Orden físico explícito define «primera»; no es una fecha de negocio.
            statement = sql.SQL("SELECT * FROM {} ORDER BY tableoid, ctid").format(sql.Identifier("public", table))
            with connection.cursor(name=f"clean_{table}") as cursor:
                cursor.execute(statement)
                chunks: list[pd.DataFrame] = []
                while rows := cursor.fetchmany(config.fetch_size):
                    columns = [column.name for column in cursor.description]
                    chunks.append(pd.DataFrame.from_records(rows, columns=columns))
                tables[table] = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=[c.name for c in cursor.description])
            print(f"Leída {table}: {len(tables[table]):,} filas.", flush=True)
    return tables


def save_cache(results: dict[str, Result], config: Config, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for table, (clean, quarantine, _) in results.items():
        clean.to_parquet(directory / f"{table}.parquet", index=False)
        quarantine.to_parquet(directory / f"{table}.quarantine.parquet", index=False)
    manifest = {"config": asdict(config), "rows": {table: {"read": s.rows_read, "clean": s.rows_clean, "quarantine": s.rows_quarantine} for table, (_, _, s) in results.items()}}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
