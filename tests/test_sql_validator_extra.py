import pytest

from glazedco.sql_validator import SQLValidationError, validate_sql

SCHEMA = {
    "sales_daily": {"store_id": "text", "sku": "text", "net_sales": "numeric", "units_sold": "integer", "date": "date"},
    "stores": {"store_id": "text", "site_id": "text"},
}


@pytest.mark.parametrize("sql", [
    "SELECT * INTO nueva FROM sales_daily",
    "SELECT store_id FROM sales_daily FOR UPDATE",
    "SELECT store_id FROM sales_daily; DROP TABLE sales_daily",
    "SELECT store_id FROM sales_daily -- x\n; DELETE FROM sales_daily",
    "WITH d AS (DELETE FROM sales_daily RETURNING store_id) SELECT store_id FROM d",
    "WITH i AS (INSERT INTO stores VALUES ('x','y') RETURNING store_id) SELECT store_id FROM i",
    "SELECT * FROM pg_catalog.pg_shadow",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pg_shadow",
    "SELECT store_id FROM sales_daily WHERE EXISTS (SELECT 1 FROM pg_shadow)",
    "SELECT store_id FROM sales_daily WHERE store_id IN (SELECT usename FROM pg_shadow)",
    "SELECT (SELECT passwd FROM pg_shadow LIMIT 1) FROM sales_daily",
    "WITH x AS (SELECT * FROM pg_shadow) SELECT * FROM x",
    "SELECT pg_sleep(10)",
    "SELECT store_id FROM sales_daily WHERE pg_sleep(10) IS NULL",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT current_setting('server_version')",
    "SELECT * FROM generate_series(1, 100000000)",
    "SELECT store_id FROM sales_daily WHERE store_id = $1",
    "SELECT store_id FROM sales_daily UNION SELECT usename FROM pg_shadow",
    "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
    "EXPLAIN ANALYZE SELECT store_id FROM sales_daily",
    "COPY sales_daily TO '/tmp/x'",
    "SET statement_timeout = 0",
])
def test_rejects_extra_attack_cases(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, SCHEMA)


def test_comment_never_reaches_output():
    out = validate_sql("SELECT store_id FROM sales_daily /* ; DROP TABLE x */", SCHEMA)
    assert "DROP" not in out.sql.upper()
    assert "/*" not in out.sql and "--" not in out.sql