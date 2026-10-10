"""Fixtures ficticias del contrato del validador; no representan datos de la BD."""

import socket

import pytest
import sqlglot
from sqlglot import exp

from glazedco.sql_validator import SQLPolicy, SQLValidationError, validate_sql

SCHEMA = {
    "sales_daily": {"store_id": "text", "net_sales": "numeric"},
    "stores": {"store_id": "text", "name": "text"},
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Las pruebas del validador no pueden usar red.")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.mark.parametrize("sql", [
    "DROP TABLE stores", "DELETE FROM stores", "UPDATE stores SET name='x'",
    "INSERT INTO stores VALUES ('1', 'x')", "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE stores", "SELECT 1;;", "", "-- comentario",
    "WITH x AS (DELETE FROM stores RETURNING *) SELECT * FROM x",
    "WITH x AS (UPDATE stores SET name='x' RETURNING *) SELECT * FROM x",
    "WITH x AS (INSERT INTO stores VALUES ('1','x') RETURNING *) SELECT * FROM x",
    "WITH x AS (SELECT 1) DELETE FROM stores", "SELECT * INTO stolen FROM stores",
    "SELECT * FROM stores FOR UPDATE", "SELECT * FROM stores FOR SHARE",
    "COPY stores TO STDOUT", "EXPLAIN SELECT * FROM stores",
    "SELECT pg_sleep(10)", "SELECT pg_read_file('/etc/passwd')",
    "SELECT set_config('statement_timeout','0',false)", "SELECT nextval('seq')",
    "SELECT pg_catalog.pg_sleep(1)", "SELECT public.sum(net_sales) FROM sales_daily",
    "SELECT * FROM generate_series(1,100)", "SELECT pg_advisory_lock(1)",
    "SELECT lo_import('/etc/passwd')", "SELECT custom_function(name) FROM stores",
    "SELECT 'x'::regclass", "WITH RECURSIVE x AS (SELECT 1) SELECT * FROM x",
    "SELECT FROM", "SELECT 1 UNION SELECT 2",
])
def test_reject_unsafe_or_unsupported_sql(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, SCHEMA)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM unknown", "SELECT secret FROM stores",
    "SELECT s.secret FROM stores s", "SELECT * FROM private.stores",
    "SELECT * FROM pg_catalog.pg_user", "SELECT * FROM other.public.stores",
    "SELECT store_id FROM stores JOIN sales_daily ON true",
    "SELECT name FROM stores WHERE secret=1", "SELECT name FROM stores ORDER BY secret",
    "WITH x AS (SELECT secret FROM stores) SELECT * FROM x",
    "WITH x AS (SELECT name FROM stores) SELECT secret FROM x",
    "SELECT (SELECT secret FROM stores) FROM sales_daily",
    'SELECT name FROM "STORES"', "SELECT stores FROM stores",
    "SELECT s.* FROM stores s WHERE s.secret=1",
    "SELECT s FROM stores s", "SELECT COUNT(s) FROM stores s",
    "SELECT name FROM stores JOIN sales_daily d ON stores.secret=d.store_id",
    "SELECT store_id, SUM(net_sales) FROM sales_daily GROUP BY store_id HAVING SUM(secret)>0",
    "WITH unused AS (SELECT * FROM unknown) SELECT name FROM stores",
    "SELECT name FROM stores WHERE EXISTS (SELECT secret FROM sales_daily)",
])
def test_reject_unknown_or_ambiguous_identifiers(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, SCHEMA)


@pytest.mark.parametrize("sql", [
    "SELECT name FROM stores", "SELECT * FROM public.stores",
    'SELECT "name" FROM "stores"', "SELECT NAME FROM STORES",
    "SELECT s.* FROM stores s", "SELECT COUNT(*) FROM stores",
    "SELECT s.name, SUM(d.net_sales) AS total FROM stores s JOIN sales_daily d "
    "ON s.store_id=d.store_id GROUP BY s.name ORDER BY total DESC",
    "WITH x AS (SELECT name FROM stores) SELECT name FROM x",
    "WITH stores AS (SELECT net_sales FROM sales_daily) SELECT * FROM stores",
    "SELECT x.name FROM (SELECT name FROM stores) x",
    "SELECT name FROM stores s WHERE EXISTS "
    "(SELECT 1 FROM sales_daily d WHERE d.store_id=s.store_id)",
    "SELECT '; DROP TABLE stores' AS text", "SELECT 1; -- comentario",
    "SELECT CASE WHEN net_sales>0 THEN net_sales ELSE 0 END FROM sales_daily",
])
def test_accept_select_and_cte(sql):
    result = validate_sql(sql, SCHEMA)
    parsed = sqlglot.parse_one(result.sql, read="postgres")
    assert isinstance(parsed, exp.Select)
    assert parsed.args["limit"].expression.this == "1000"
    assert result.statement_timeout_ms == 5000


@pytest.mark.parametrize(("suffix", "expected"), [
    ("", 25), (" LIMIT 100", 25), (" LIMIT 4", 4), (" LIMIT 0", 0),
    (" LIMIT ALL", 25), (" LIMIT NULL", 25), (" LIMIT 4 OFFSET 2", 4),
])
def test_limit_and_timeout(suffix, expected):
    result = validate_sql("SELECT name FROM stores" + suffix, SCHEMA,
                          policy=SQLPolicy(max_rows=25, statement_timeout_ms=1234))
    parsed = sqlglot.parse_one(result.sql, read="postgres")
    assert int(parsed.args["limit"].expression.this) == expected
    assert result.statement_timeout_ms == 1234


@pytest.mark.parametrize("suffix", [" LIMIT -1", " LIMIT 1+2", " LIMIT $1",
                                         " FETCH FIRST 2 ROWS WITH TIES"])
def test_reject_nonliteral_limit(suffix):
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT name FROM stores" + suffix, SCHEMA)


def test_star_expansion_and_qualified_tables():
    result = validate_sql("SELECT * FROM stores", SCHEMA)
    parsed = sqlglot.parse_one(result.sql, read="postgres")
    assert not list(parsed.find_all(exp.Star))
    assert {c.name for c in parsed.find_all(exp.Column)} == {"store_id", "name"}
    assert parsed.find(exp.Table).db == "public"


def test_limit_is_on_outer_query():
    result = validate_sql("WITH x AS (SELECT name FROM stores LIMIT 1) SELECT * FROM x", SCHEMA,
                          policy=SQLPolicy(max_rows=7))
    parsed = sqlglot.parse_one(result.sql, read="postgres")
    assert parsed.args["limit"].expression.this == "7"
    assert parsed.find(exp.CTE).this.args["limit"].expression.this == "1"


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100", 2**31, None])
@pytest.mark.parametrize("field", ["max_rows", "statement_timeout_ms"])
def test_invalid_policy(field, value):
    with pytest.raises(ValueError):
        SQLPolicy(**{field: value})


def test_empty_schema():
    with pytest.raises(ValueError):
        validate_sql("SELECT 1", {})


def test_custom_schema_and_exact_quoted_names():
    result = validate_sql('SELECT "Name" FROM "Stores"', {"Stores": {"Name": "text"}},
                          policy=SQLPolicy(schema_name="retail"))
    parsed = sqlglot.parse_one(result.sql, read="postgres")
    assert parsed.find(exp.Table).db == "retail"
    assert parsed.find(exp.Column).name == "Name"


def test_validation_is_idempotent():
    first = validate_sql("SELECT * FROM stores", SCHEMA)
    assert validate_sql(first.sql, SCHEMA) == first
