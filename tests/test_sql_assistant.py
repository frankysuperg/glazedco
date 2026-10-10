"""MCP en memoria, Gemini simulado y conexiones simuladas; sin servicios reales."""

import asyncio
from contextlib import contextmanager
from decimal import Decimal
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastmcp import Client
import pytest

from glazedco.db import Database
from glazedco.sql_validator import SQLPolicy, ValidatedSQL, validate_sql
from glazedco.sql_assistant import service as service_module
from glazedco.sql_assistant.assistant import UNSAFE, UNANSWERABLE, answer_question
from glazedco.sql_assistant.config import ALLOWED_TABLES, load_policy
from glazedco.sql_assistant.explanation import render_explanation
from glazedco.sql_assistant.server import create_server
from glazedco.sql_assistant.service import RAW_WARNING, SQLService


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Windows crea un socketpair interno para asyncio antes de bloquear conexiones.
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio.events, "new_event_loop", lambda: loop)
    def blocked(*args, **kwargs):
        raise AssertionError("Prohibidas conexiones de red en estas pruebas.")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr("glazedco.db.load_environment", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/mock")
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    yield
    if not loop.is_closed():
        loop.close()


@pytest.fixture
def setup(monkeypatch):
    pool = MagicMock()
    monkeypatch.setattr("glazedco.db.ConnectionPool", MagicMock(return_value=pool))
    connection = pool.connection.return_value.__enter__.return_value
    state = SimpleNamespace(active=False, transactions=0, calls=[], rows=[(Decimal("12.50"),)],
                            columns=["total"], fail=False, fetched=[])

    @contextmanager
    def transaction(*, force_rollback):
        assert force_rollback is True
        assert connection.read_only is True and connection.autocommit is False
        state.active = True
        state.transactions += 1
        try:
            yield
        finally:
            state.active = False

    connection.transaction.side_effect = transaction
    records = [(table, "id", "integer") for table in ALLOWED_TABLES]
    records += [("stores", "name", "text"), ("sales_daily", "net_sales", "numeric")]
    records += [(name, "secret", "text") for name in
                ("stg_sales", "rejects_sales", "sales_daily_original", "weather_daily")]

    def execute(sql, *params):
        assert state.active
        state.calls.append((sql, params))
        if sql == service_module.CATALOG_SQL:
            cursor = MagicMock()
            cursor.fetchall.return_value = records
            return cursor
        if sql.startswith("SET "):
            return MagicMock()
        if state.fail:
            raise RuntimeError("secret credentials must not escape")
        cursor = MagicMock()
        cursor.description = [SimpleNamespace(name=name) for name in state.columns]
        def fetchmany(size):
            state.fetched.append(size)
            return state.rows[:size]
        cursor.fetchmany.side_effect = fetchmany
        return cursor

    connection.execute.side_effect = execute
    database = Database(assistant=True)
    service = SQLService(database, SQLPolicy(max_rows=3, statement_timeout_ms=4321))
    state.catalog_calls = list(state.calls)
    state.calls.clear()
    state.transactions = 0
    return service, state, connection, records


@pytest.mark.parametrize("sql", [
    "DROP TABLE stores", "DELETE FROM stores", "UPDATE stores SET name='x'",
    "INSERT INTO stores VALUES (1,'x')", "SELECT 1; SELECT 2",
    "SELECT * FROM weather_daily", "SELECT * FROM stg_sales",
    "SELECT * FROM rejects_sales", "SELECT * FROM sales_daily_original",
])
def test_rejections_never_execute(setup, sql):
    service, state, *_ = setup
    assert service.run_readonly_sql(sql).status == "rejected"
    assert state.calls == [] and state.transactions == 0


@pytest.mark.parametrize("error", [ValueError, RuntimeError, KeyError, AttributeError])
def test_unexpected_validator_exceptions_fail_closed(setup, monkeypatch, error):
    service, state, *_ = setup
    def fail(*args, **kwargs):
        raise error("sensitive details")
    monkeypatch.setattr(service_module, "validate_sql", fail)
    assert service.run_readonly_sql("SELECT name FROM stores").status == "rejected"
    assert state.calls == [] and state.transactions == 0


def test_exact_validated_sql_transaction_timeout_and_limit(setup):
    service, state, *_ = setup
    original = "SELECT SUM(net_sales) AS total FROM sales_daily LIMIT 9999"
    expected = validate_sql(original, service.get_schema(), policy=service.policy)
    result = service.run_readonly_sql(original)
    assert result.status == "ok" and result.rows == [["12.50"]]
    assert state.calls == [
        ("SET TRANSACTION READ ONLY", ()),
        ("SET LOCAL statement_timeout = 4321", ()),
        ("SET LOCAL search_path = pg_catalog", ()),
        (expected.sql, ()),
    ]
    assert original != expected.sql
    assert state.fetched == [3]
    assert not state.active and state.transactions == 1


def test_only_regenerated_text_is_used(setup, monkeypatch):
    service, state, *_ = setup
    monkeypatch.setattr(service_module, "validate_sql", lambda *a, **kw: ValidatedSQL("SELECT 7 LIMIT 3", 321))
    service.run_readonly_sql("arbitrary untrusted model text")
    assert state.calls[-1] == ("SELECT 7 LIMIT 3", ())
    assert state.calls[1] == ("SET LOCAL statement_timeout = 321", ())


def test_execution_failure_is_generic_and_exits_transaction(setup):
    service, state, *_ = setup
    state.fail = True
    result = service.run_readonly_sql("SELECT name FROM stores")
    assert result.status == "error" and "secret" not in str(result)
    assert not state.active


def test_schema_is_explicit_filtered_and_defensive(setup):
    service, state, *_ = setup
    assert len(ALLOWED_TABLES) == 13
    schema = service.get_schema()
    assert set(schema) == set(ALLOWED_TABLES)
    assert schema["stores"]["name"] == "text"
    assert state.catalog_calls[-1][1] == (("public", list(ALLOWED_TABLES)),)
    assert state.catalog_calls[:3] == [
        ("SET TRANSACTION READ ONLY", ()),
        ("SET LOCAL statement_timeout = 4321", ()),
        ("SET LOCAL search_path = pg_catalog", ()),
    ]
    schema["stores"]["secret"] = "text"
    assert "secret" not in service.get_schema()["stores"]
    assert state.calls == []


def test_missing_catalog_columns_fail_startup(setup):
    service, state, connection, records = setup
    records[:] = [row for row in records if row[0] != "stores"]
    with pytest.raises(ValueError, match="incompleto"):
        SQLService(service._database, service.policy)


def test_readonly_url_alone_and_fallback(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr("glazedco.db.ConnectionPool", factory)
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL_READONLY", "postgresql://readonly.invalid/mock")
    assert Database(assistant=True).source == "DATABASE_URL_READONLY"
    assert factory.call_args.kwargs["conninfo"] == "postgresql://readonly.invalid/mock"
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary.invalid/mock")
    monkeypatch.delenv("DATABASE_URL_READONLY")
    assert Database(assistant=True).source == "DATABASE_URL"
    assert factory.call_args.kwargs["conninfo"] == "postgresql://primary.invalid/mock"


def run_answer(service, responses):
    gemini = MagicMock()
    gemini.generate.side_effect = responses
    async def run():
        async with Client(create_server(service=service)) as client:
            return await answer_question("¿Cuál es el total?", gemini=gemini, client=client)
    return asyncio.run(run()), gemini


def test_mcp_contract_in_memory(setup):
    service, state, *_ = setup
    async def run():
        async with Client(create_server(service=service)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == {"get_schema", "run_readonly_sql", "get_trending_hashtags"}
            for tool in tools:
                assert tool.annotations.readOnlyHint is True
            schema = (await client.call_tool("get_schema", {})).data
            assert set(schema) == set(ALLOWED_TABLES)
            result = (await client.call_tool("run_readonly_sql", {"sql": "DELETE FROM stores"})).structured_content
            assert result["status"] == "rejected"
    asyncio.run(run())
    assert state.calls == []


@pytest.mark.parametrize("failure", [False, True])
def test_server_lifespan_closes_database(setup, monkeypatch, failure):
    service, *_ = setup
    factory = MagicMock()
    monkeypatch.setattr("glazedco.sql_assistant.server.Database", factory)
    loader = MagicMock(return_value=service)
    if failure:
        loader.side_effect = RuntimeError("secret")
    monkeypatch.setattr("glazedco.sql_assistant.server.SQLService", loader)
    async def run():
        async with Client(create_server(policy=service.policy)) as client:
            await client.call_tool("get_schema", {})
    if failure:
        with pytest.raises(Exception) as error:
            asyncio.run(run())
        assert "secret" not in str(error.value)
    else:
        asyncio.run(run())
    factory.assert_called_once_with(assistant=True)
    factory.return_value.__enter__.assert_called_once()
    factory.return_value.__exit__.assert_called_once()
    loader.assert_called_once()


def test_flow_retries_once_then_uses_analysis(setup):
    service, state, *_ = setup
    result, gemini = run_answer(service, [
        "DROP TABLE stores", "SELECT SUM(net_sales) AS total FROM sales_daily",
        '{"hechos":[{"fila":0,"columna":0}]}',
    ])
    assert "12.50" in result and RAW_WARNING in result
    assert [c.kwargs["profile"] for c in gemini.generate.call_args_list] == ["sql", "sql", "analysis"]
    assert "rechazada por seguridad" in gemini.generate.call_args_list[1].args[0]
    assert "DROP TABLE" not in gemini.generate.call_args_list[1].args[0]
    assert "pregunta" not in gemini.generate.call_args_list[2].args[0]
    assert state.transactions == 1


def test_second_rejection_stops(setup):
    service, state, *_ = setup
    result, gemini = run_answer(service, ["DELETE FROM stores", "DROP TABLE stores"])
    assert result == UNSAFE and gemini.generate.call_count == 2
    assert state.calls == []


def test_unexpected_validation_failure_is_also_retried_once(setup, monkeypatch):
    service, state, *_ = setup
    monkeypatch.setattr(service_module, "validate_sql", MagicMock(side_effect=RuntimeError("secret")))
    result, gemini = run_answer(service, ["SELECT name FROM stores"] * 2)
    assert result == UNSAFE and gemini.generate.call_count == 2
    assert state.calls == []


def test_unanswerable_does_not_execute(setup):
    service, state, *_ = setup
    result, gemini = run_answer(service, ["no tengo ese dato"])
    assert result == UNANSWERABLE and gemini.generate.call_count == 1
    assert state.calls == []


def test_empty_results_do_not_invent_explanation(setup):
    service, state, *_ = setup
    state.rows = []
    result, gemini = run_answer(service, ["SELECT name FROM stores"])
    assert "no devolvió filas" in result and RAW_WARNING in result
    assert gemini.generate.call_count == 1


def test_execution_errors_do_not_trigger_sql_retry(setup):
    service, state, *_ = setup
    state.fail = True
    result, gemini = run_answer(service, ["SELECT name FROM stores"])
    assert "No se pudo ejecutar" in result and "secret" not in result
    assert gemini.generate.call_count == 1


def test_gemini_failure_is_redacted(setup):
    service, state, *_ = setup
    result, _ = run_answer(service, [RuntimeError("secret API key")])
    assert result == UNSAFE and state.calls == []


@pytest.mark.parametrize("response", [
    "Los ingresos fueron 999999 y aumentaron cien por ciento.",
    '{"hechos":[{"fila":999999,"columna":0}]}',
    '{"hechos":[{"fila":0,"columna":0,"valor":999999}]}',
    '{"hechos":[{"fila":-1,"columna":0}]}',
    '{"hechos":[{"fila":true,"columna":0}]}',
    '{"hechos":[], "explicacion":"cien por ciento"}',
])
def test_unbacked_figures_never_reach_explanation(setup, response):
    service, state, *_ = setup
    result, _ = run_answer(service, ["SELECT SUM(net_sales) AS total FROM sales_daily", response])
    assert "999999" not in result and "cien" not in result
    assert "explicación verificable" in result and RAW_WARNING in result


def test_null_does_not_become_zero():
    result = render_explanation('{"hechos":[{"fila":0,"columna":0}]}', ["total"], [[None]])
    assert "no tengo ese dato" in result and "0" not in result


def test_config(monkeypatch):
    monkeypatch.setattr("glazedco.sql_assistant.config.load_environment", lambda: None)
    monkeypatch.setenv("SQL_MAX_ROWS", "9")
    monkeypatch.setenv("SQL_STATEMENT_TIMEOUT_MS", "150")
    monkeypatch.setenv("SQL_SCHEMA", "retail")
    assert load_policy() == SQLPolicy(max_rows=9, statement_timeout_ms=150, schema_name="retail")
    monkeypatch.setenv("SQL_STATEMENT_TIMEOUT_MS", "secret_invalid_value")
    with pytest.raises(ValueError, match="Configuración SQL inválida") as error:
        load_policy()
    assert "secret" not in str(error.value)
