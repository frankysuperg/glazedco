"""Catálogo acotado y ejecución exclusiva del SQL regenerado por el validador."""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
import math
from typing import Literal, TypeAlias

from psycopg import Connection

from glazedco.db import Database
from glazedco.sql_validator import SQLPolicy, ValidatedSQL, validate_sql
from .config import ALLOWED_TABLES

Cell: TypeAlias = str | int | float | bool | None
RAW_WARNING = (
    "Estas consultas usan tablas sin limpiar; sus totales pueden diferir de los del "
    "dashboard. Consulta docs/cleaning_report.md."
)
CATALOG_SQL = """
SELECT c.table_name, c.column_name, c.data_type
FROM information_schema.columns AS c
JOIN information_schema.tables AS t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = %s AND c.table_name = ANY(%s)
  AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""


def _cell(value: object) -> Cell:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)  # No convertir importes a float.
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    raise ValueError("Tipo de resultado no admitido.")


@dataclass(frozen=True)
class QueryResult:
    status: Literal["ok", "rejected", "error"]
    columns: list[str] = field(default_factory=list)
    rows: list[list[Cell]] = field(default_factory=list)
    warning: str = RAW_WARNING


def _configure_transaction(connection: Connection, timeout_ms: int) -> None:
    # Solo valores enteros validados; SET no admite placeholders PostgreSQL.
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 2_147_483_647:
        raise ValueError("Timeout inválido.")
    connection.execute("SET TRANSACTION READ ONLY")
    connection.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
    connection.execute("SET LOCAL search_path = pg_catalog")


class SQLService:
    def __init__(self, database: Database, policy: SQLPolicy) -> None:
        self._database = database
        self.policy = policy
        # Una lectura al iniciar, nunca descubrimiento dinámico de tablas.
        with database.transaction() as connection:
            _configure_transaction(connection, policy.statement_timeout_ms)
            records = connection.execute(
                CATALOG_SQL, (policy.schema_name, list(ALLOWED_TABLES)),
            ).fetchall()
        self._schema: dict[str, dict[str, str]] = {name: {} for name in ALLOWED_TABLES}
        for table, column, data_type in records:
            if table in self._schema:  # Defensa adicional incluso con un catálogo simulado.
                self._schema[table][column] = data_type
        if any(not columns for columns in self._schema.values()):
            raise ValueError("El catálogo autorizado está incompleto o no es accesible.")

    def get_schema(self) -> dict[str, dict[str, str]]:
        return {table: dict(columns) for table, columns in self._schema.items()}

    def run_readonly_sql(self, sql: str) -> QueryResult:
        try:
            validated = validate_sql(sql, self.get_schema(), policy=self.policy)
            if not isinstance(validated, ValidatedSQL):
                raise ValueError("Resultado de validación inválido.")
        except Exception:
            # Incluye fallos imprevistos del parser: no adquirir conexión ni ejecutar.
            return QueryResult(status="rejected")
        try:
            with self._database.transaction() as connection:
                _configure_transaction(connection, validated.statement_timeout_ms)
                cursor = connection.execute(validated.sql)  # Exactamente esto, sin parámetros.
                columns = [column.name for column in cursor.description]
                rows = [[_cell(value) for value in row]
                        for row in cursor.fetchmany(self.policy.max_rows)]
            return QueryResult(status="ok", columns=columns, rows=rows)
        except Exception:
            # Nunca enviar errores del servidor, SQL ni credenciales al modelo/MCP.
            return QueryResult(status="error")
