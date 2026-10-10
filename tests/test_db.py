from unittest.mock import MagicMock, patch

import psycopg
import pytest

from glazedco.db import Database, DatabaseError, SafeConnection, load_environment, safe_error
from glazedco.db_check import check_database, main


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/test")
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    with patch("glazedco.db.load_dotenv") as loader:
        yield loader


def test_requires_primary_url(environment, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(DatabaseError, match="Falta DATABASE_URL"):
        Database()


def test_dotenv_loading_failure_does_not_expose_contents() -> None:
    with patch("glazedco.db.load_dotenv", side_effect=UnicodeError("secret")):
        with pytest.raises(DatabaseError) as captured:
            load_environment()
    assert "secret" not in str(captured.value)
    assert "UTF-8" in str(captured.value)


@pytest.mark.parametrize("assistant,dedicated", [(False, False), (False, True), (True, False), (True, True)])
def test_configuration_and_url_selection(environment, monkeypatch, assistant, dedicated) -> None:
    if dedicated:
        monkeypatch.setenv("DATABASE_URL_READONLY", "postgresql://readonly.invalid/test")
    with patch("glazedco.db.ConnectionPool") as pool:
        database = Database(assistant=assistant)
    config = pool.call_args.kwargs
    assert config["conninfo"] == (
        "postgresql://readonly.invalid/test" if assistant and dedicated
        else "postgresql://example.invalid/test"
    )
    assert database.assistant_source == ("DATABASE_URL_READONLY" if dedicated else "DATABASE_URL")
    assert config["kwargs"]["sslmode"] == "require"
    assert config["kwargs"]["connect_timeout"] == 10
    assert "statement_timeout=15000" in config["kwargs"]["options"]
    assert "default_transaction_read_only=on" in config["kwargs"]["options"]
    assert (config["min_size"], config["max_size"]) == (0, 2)
    assert config["timeout"] == 15
    assert config["open"] is False
    assert environment.call_args.kwargs["override"] is False
    assert environment.call_args.args[0].name == ".env"


@pytest.mark.parametrize("fails", [False, True])
def test_transaction_is_readonly_and_rolls_back(environment, fails) -> None:
    with patch("glazedco.db.ConnectionPool") as factory:
        pool = factory.return_value
        connection = pool.connection.return_value.__enter__.return_value
        with Database() as database:
            if fails:
                with pytest.raises(DatabaseError):
                    with database.transaction():
                        raise psycopg.OperationalError("postgresql://user:secret@private-host")
            else:
                with database.transaction():
                    assert connection.read_only is True
                    assert connection.autocommit is False
        connection.transaction.assert_called_once_with(force_rollback=True)
        pool.open.assert_called_once()
        pool.close.assert_called_once()


def test_pool_errors_never_expose_connection_details() -> None:
    with patch.object(psycopg.Connection, "connect", side_effect=psycopg.OperationalError(
        "SSL failure postgresql://user:secret@private-host"
    )):
        with pytest.raises(psycopg.OperationalError) as captured:
            SafeConnection.connect("postgresql://user:secret@private-host")
    assert "SSL" in str(captured.value)
    assert "secret" not in str(captured.value)
    assert "private-host" not in str(captured.value)


@pytest.mark.parametrize("timescale", [False, True])
def test_check_catalog_and_parameterized_schema(timescale: bool) -> None:
    database = MagicMock()
    database.source = database.assistant_source = "DATABASE_URL"
    connection = database.transaction.return_value.__enter__.return_value
    connection.pgconn.ssl_in_use = True
    schema = "project'; DROP TABLE ignored; --"
    responses = [MagicMock() for _ in range(6 if timescale else 5)]
    responses[0].fetchone.return_value = (1,)
    responses[1].fetchone.return_value = ("PostgreSQL mocked", "public", "on")
    responses[2].fetchone.return_value = (1,)
    responses[3].fetchone.return_value = (1,) if timescale else None
    if timescale:
        responses[4].fetchall.return_value = [("sales",)]
    responses[-1].fetchall.return_value = [("sales", 42), ("unknown", None)]
    connection.execute.side_effect = responses
    result = check_database(database, schema)
    assert result.schema == schema
    assert result.tables[0].approximate_rows == 42
    assert result.tables[0].hypertable is timescale
    assert result.tables[1].approximate_rows is None
    for call in connection.execute.call_args_list:
        query = call.args[0]
        assert query.lstrip().upper().startswith(("SELECT", "WITH RECURSIVE"))
        assert schema not in query
        assert "COUNT(" not in query.upper()
    assert connection.execute.call_args.args[1] == (schema,)


@pytest.mark.parametrize("ssl,readonly", [(False, "on"), (True, "off")])
def test_check_rejects_unprotected_connection(ssl, readonly) -> None:
    database = MagicMock()
    connection = database.transaction.return_value.__enter__.return_value
    connection.pgconn.ssl_in_use = ssl
    connection.execute.return_value.fetchone.side_effect = [
        (1,), ("mock", "public", readonly), (1,),
    ]
    with pytest.raises(DatabaseError, match="SSL"):
        check_database(database)


def test_cli_failure_is_clear_and_redacted(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["db_check"])
    with patch("glazedco.db_check.Database", side_effect=DatabaseError(
        safe_error(psycopg.OperationalError("password=secret host=private"))
    )):
        assert main() == 1
    output = capsys.readouterr().out
    assert "secret" not in output and "private" not in output
    assert "SSL" in output and "red" in output and "credenciales" in output
