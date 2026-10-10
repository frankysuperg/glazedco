"""Validación local y conservadora de SELECT PostgreSQL; nunca ejecuta SQL."""

from collections.abc import Mapping
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

AllowedSchema = Mapping[str, Mapping[str, str]]


class SQLValidationError(ValueError):
    """Consulta fuera del subconjunto de SQL permitido."""


@dataclass(frozen=True)
class SQLPolicy:
    max_rows: int = 1000
    statement_timeout_ms: int = 5000
    schema_name: str = "public"

    def __post_init__(self) -> None:
        for value in (self.max_rows, self.statement_timeout_ms):
            if type(value) is not int or not 1 <= value <= 2_147_483_647:
                raise ValueError("LIMIT y timeout deben ser enteros positivos de 32 bits.")
        if not self.schema_name:
            raise ValueError("Se requiere un esquema PostgreSQL explícito.")


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    statement_timeout_ms: int


# Lista cerrada: cualquier nueva construcción requiere revisión y pruebas.
_NODES = frozenset({
    "Select", "From", "Table", "Column", "Identifier", "Star", "Alias",
    "TableAlias", "With", "CTE", "Subquery", "Join", "Where", "Group",
    "Having", "Order", "Ordered", "Limit", "Offset", "Distinct", "Paren",
    "Literal", "Null", "Boolean", "And", "Or", "Not", "EQ", "NEQ",
    "GT", "GTE", "LT", "LTE", "Is", "In", "Between", "Like", "ILike",
    "Add", "Sub", "Mul", "Div", "Mod", "Neg", "Tuple",
})
_FUNCTIONS = frozenset({
    "Count", "Sum", "Avg", "Min", "Max", "Abs", "Round", "Coalesce",
    "Nullif", "Lower", "Upper", "Length", "Case", "If", "Exists",
})


def _check_nodes(query: exp.Expression) -> None:
    for node in query.walk():
        if (isinstance(node, exp.Var) and node.name.upper() == "ALL"
                and isinstance(node.parent, exp.Limit)):
            continue
        permitted = _FUNCTIONS if isinstance(node, exp.Func) else _NODES
        if type(node).__name__ not in permitted:
            raise SQLValidationError("Construcción o función SQL no permitida.")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SQLValidationError("No se permiten CTE recursivas.")
        if isinstance(node, exp.Column) and (node.db or node.catalog):
            raise SQLValidationError("Usa columnas sin esquema o calificadas por alias.")


def _limit(query: exp.Select, max_rows: int) -> None:
    limit = query.args.get("limit")
    rows = max_rows
    if limit is not None:
        value = limit.expression
        if isinstance(value, exp.Null) or (isinstance(value, exp.Var) and value.name.upper() == "ALL"):
            pass  # PostgreSQL LIMIT ALL / NULL: sin límite original.
        elif isinstance(value, exp.Literal) and not value.is_string and value.this.isdigit():
            rows = min(int(value.this), max_rows)
        else:
            raise SQLValidationError("LIMIT debe ser un entero no negativo o ALL/NULL.")
    query.set("limit", exp.Limit(expression=exp.Literal.number(rows)))


def _check_columns(query: exp.Select, allowed_schema: AllowedSchema) -> None:
    # qualify no garantiza resolver todas las columnas dentro de HAVING.
    for scope in traverse_scope(query):
        for column in scope.expression.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not scope.expression:
                continue
            if not column.table:
                if (isinstance(column.parent, exp.Ordered)
                        and column.name in scope.expression.named_selects):
                    continue
                raise SQLValidationError("Columna sin origen autorizado.")
            owner: Scope | None = scope
            while owner is not None and column.table not in owner.sources:
                owner = owner.parent
            if owner is None:
                raise SQLValidationError("Alias de columna desconocido.")
            source = owner.sources[column.table]
            columns = (allowed_schema[source.name] if isinstance(source, exp.Table)
                       else source.expression.named_selects)
            if column.name not in columns:
                raise SQLValidationError("Columna no permitida.")


def validate_sql(
    sql: str, allowed_schema: AllowedSchema, *, policy: SQLPolicy | None = None,
) -> ValidatedSQL:
    """Devuelve SQL con tablas calificadas, columnas verificadas y LIMIT externo.

    allowed_schema contiene nombres exactos de tablas y columnas, y sus tipos;
    se suministra desde configuración confiable, nunca desde el modelo. Solo
    permite tablas de policy.schema_name. Expande * a las columnas autorizadas.
    El timeout es un requisito para el futuro ejecutor, no un temporizador local.
    Ese ejecutor deberá aplicarlo dentro de una transacción/rol de solo lectura.
    """
    policy = policy or SQLPolicy()
    if not allowed_schema or any(not columns for columns in allowed_schema.values()):
        raise ValueError("El esquema permitido debe contener tablas con columnas.")
    try:
        statements = [node for node in sqlglot.parse(sql, read="postgres")
                      if not isinstance(node, exp.Semicolon)]
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise SQLValidationError("Se requiere una sola sentencia SELECT o WITH SELECT.")
        query = normalize_identifiers(statements[0], dialect="postgres")
        _check_nodes(query)
        _limit(query, policy.max_rows)
        for scope in traverse_scope(query):
            for source in scope.sources.values():
                if isinstance(source, Scope):
                    continue
                if not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier):
                    raise SQLValidationError("Origen de datos no permitido.")
                if (source.catalog or source.db not in ("", policy.schema_name)
                        or source.name not in allowed_schema):
                    raise SQLValidationError("Tabla o esquema no permitido.")
                source.set("db", exp.to_identifier(policy.schema_name, quoted=True))
        # UNKNOWN evita interpretar tipos proporcionados como código SQL.
        schema = {policy.schema_name: {
            table: {column: "UNKNOWN" for column in columns}
            for table, columns in allowed_schema.items()
        }}
        query = qualify(query, dialect="postgres",
                        schema=MappingSchema(schema, dialect="postgres", normalize=False),
                        validate_qualify_columns=True, allow_partial_qualification=False)
        _check_nodes(query)  # La resolución puede introducir referencias a filas completas.
        _check_columns(query, allowed_schema)
        # Nunca dejar comodines sin resolver (COUNT(*) sí es seguro).
        for star in query.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                raise SQLValidationError("No se pudo resolver el comodín autorizado.")
        return ValidatedSQL(query.sql(dialect="postgres", comments=False),
                            policy.statement_timeout_ms)
    except (SqlglotError, RecursionError) as exc:
        raise SQLValidationError("SQL inválido o columnas desconocidas/ambiguas.") from exc
