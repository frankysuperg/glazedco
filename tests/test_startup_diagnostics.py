"""Diagnóstico privado sanitizado y timeout de carga aislado, sin red."""

from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient

from test_api import MemorySource, block_services  # noqa: F401
from test_api_startup import wait_finished
from glazedco.api.app import create_app
from glazedco.api.data import Dashboard
from glazedco.api.diagnostics import sanitized_traceback, redact
from glazedco.cleaning.config import load_config
from glazedco.cleaning.io import read_tables
from glazedco.db import Database, DatabaseError, load_timeout_ms
from glazedco.sql_assistant.service import SQLService
from glazedco.sql_validator import SQLPolicy


def test_original_exception_traceback_survives_public_wrappers(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "very-private-api-key")
    source = MemorySource()
    def failed_read():
        try:
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout; "
                "postgresql://user:password@hidden/db very-private-api-key")
        except psycopg.Error:
            raise DatabaseError("PostgreSQL: consulta cancelada.") from None
    source.tables = failed_read
    with TestClient(create_app(dashboard=Dashboard(source))) as client:
        state = wait_finished(client)
        assert state["tipo_error"] == "QueryCanceled"
        assert "statement timeout" not in state["detalle"]
    log = capsys.readouterr().err
    for expected in ("QueryCanceled", "DatabaseError", "APIError", "failed_read", "tables", "warmup", "prepare",
                     "statement timeout", "Traceback (most recent call last):"):
        assert expected in log
    for secret in ("very-private-api-key", "postgresql://", "hidden/db", "user:password"):
        assert secret not in log


@pytest.mark.parametrize("message,secret", [
    ("password='two words' host=private dbname=sample", "two words"),
    ('{"api_key": "private-token"}', "private-token"),
    ("Authorization: Bearer hidden-token", "hidden-token"),
    ("token=hidden-token; retry failed", "hidden-token"),
    ("postgresql://u:p@private/db?sslmode=require", "private/db"),
])
def test_redaction_patterns(message, secret):
    result = redact(message)
    assert secret not in result and "REDACTED" in result


def test_traceback_omits_locals_and_source_literals():
    try:
        private_key = "literal-not-to-be-logged"
        raise RuntimeError("failure without credentials")
    except RuntimeError as exc:
        log = sanitized_traceback(exc)
    assert private_key not in log
    assert "test_traceback_omits_locals_and_source_literals" in log
    assert "failure without credentials" in log


@pytest.mark.parametrize("value,expected", [(None, 300000), ("120000", 120000)])
def test_bulk_timeout_is_local_and_assistant_stays_at_five_seconds(monkeypatch, value, expected):
    monkeypatch.setattr("glazedco.db.load_environment", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/test")
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    if value is None:
        monkeypatch.delenv("SQL_LOAD_TIMEOUT_MS", raising=False)
    else:
        monkeypatch.setenv("SQL_LOAD_TIMEOUT_MS", value)
    pool = MagicMock()
    monkeypatch.setattr("glazedco.db.ConnectionPool", pool)
    connection = pool.return_value.connection.return_value.__enter__.return_value
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchmany.return_value = []
    cursor.description = []
    def reading(*args):
        assert connection.read_only is True
        connection.execute.assert_called_once_with(f"SET LOCAL statement_timeout = {expected}")
    cursor.execute.side_effect = reading
    with Database(assistant=True) as database:
        read_tables(database, load_config())
        service = SQLService.__new__(SQLService)
        service._database = database
        service.policy = SQLPolicy()
        service._schema = {"products": {"sku": "text"}}
        connection.execute.return_value.description = []
        connection.execute.return_value.fetchmany.return_value = []
        assert service.run_readonly_sql("SELECT sku FROM products").status == "ok"
    assert "statement_timeout=15000" in pool.call_args.kwargs["kwargs"]["options"]
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert statements[0] == f"SET LOCAL statement_timeout = {expected}"
    assert "SET LOCAL statement_timeout = 5000" in statements
    assert connection.transaction.call_count == 2
    assert all(call.kwargs == {"force_rollback": True} for call in connection.transaction.call_args_list)


@pytest.mark.parametrize("value", ["0", "-1", "2147483648", "1.5", "", "secret-invalid-setting"])
def test_invalid_bulk_timeout_fails_before_transaction_without_echo(monkeypatch, value):
    monkeypatch.setattr("glazedco.db.load_environment", lambda: None)
    monkeypatch.setenv("SQL_LOAD_TIMEOUT_MS", value)
    database = MagicMock()
    with pytest.raises(DatabaseError) as caught:
        read_tables(database, load_config())
    database.transaction.assert_not_called()
    assert "secret-invalid-setting" not in sanitized_traceback(caught.value)
