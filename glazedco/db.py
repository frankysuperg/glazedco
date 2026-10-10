"""Conexiones de solo lectura; nunca registrar credenciales ni errores sin filtrar."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import os
from typing import Any, Self

from dotenv import load_dotenv
import psycopg
from psycopg_pool import ConnectionPool


class DatabaseError(RuntimeError):
    """Error seguro para mostrar al usuario."""


def load_environment() -> None:
    """Carga local automática; las variables del proceso tienen prioridad."""
    try:
        load_dotenv(
            Path(__file__).resolve().parents[1] / ".env",
            override=False,
            encoding="utf-8-sig",
        )
    except (OSError, UnicodeError):
        raise DatabaseError("No se pudo cargar .env; revisa acceso y codificación UTF-8.") from None


def load_timeout_ms() -> int:
    """Timeout exclusivo de la carga masiva; no modifica el pool ni SQLPolicy."""
    load_environment()
    raw = os.getenv("SQL_LOAD_TIMEOUT_MS", "300000").strip()
    # No producir un ValueError que incluya el valor del entorno en su mensaje.
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 10 or not 1 <= int(raw) <= 2_147_483_647:
        raise DatabaseError("SQL_LOAD_TIMEOUT_MS debe ser un entero positivo válido en milisegundos.")
    value = int(raw)
    return value


def safe_error(exc: Exception) -> str:
    """Clasifica sin devolver texto del servidor, URL, usuario ni contraseña."""
    code = getattr(exc, "sqlstate", None)
    message = str(exc).lower()
    if code in {"28P01", "28000"} or "authentication failed" in message:
        return "PostgreSQL: autenticación rechazada; revisa las credenciales."
    if "ssl" in message or "certificate" in message:
        return "PostgreSQL: fallo SSL; revisa soporte TLS y certificados."
    if code == "42501":
        return "PostgreSQL: permisos insuficientes para consultar el esquema o catálogo."
    if code == "57014":
        return "PostgreSQL: consulta cancelada o timeout de sentencia alcanzado."
    return (
        "PostgreSQL: no se pudo completar la conexión o consulta. "
        "Revisa red/DNS/firewall, disponibilidad, SSL y credenciales."
    )


class SafeConnection(psycopg.Connection):
    """Evita que el pool registre errores de conexión con información sensible."""

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs: Any) -> Self:
        try:
            return super().connect(conninfo, **kwargs)
        except (psycopg.Error, ValueError) as exc:
            raise psycopg.OperationalError(safe_error(exc)) from None


class Database:
    """Pool acotado. Usar assistant=True para futuras consultas de lenguaje natural."""

    def __init__(self, *, assistant: bool = False) -> None:
        load_environment()
        primary = os.getenv("DATABASE_URL", "").strip()
        readonly = os.getenv("DATABASE_URL_READONLY", "").strip()
        if not primary and not (assistant and readonly):
            raise DatabaseError("Falta DATABASE_URL en el entorno o en .env.")
        self.assistant_source = "DATABASE_URL_READONLY" if readonly else "DATABASE_URL"
        self.source = self.assistant_source if assistant else "DATABASE_URL"
        self._pool = ConnectionPool(
            conninfo=readonly if assistant and readonly else primary,
            connection_class=SafeConnection,
            kwargs={
                "sslmode": "require",
                "connect_timeout": 10,
                "options": "-c statement_timeout=15000 -c default_transaction_read_only=on",
            },
            min_size=0,
            max_size=2,
            timeout=15,
            reconnect_timeout=0,
            num_workers=1,
            open=False,
        )

    def __enter__(self) -> Self:
        self._pool.open()
        return self

    def __exit__(self, *args: object) -> None:
        self._pool.close()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """BEGIN READ ONLY y rollback al salir, incluso si no hubo errores."""
        try:
            with self._pool.connection() as connection:
                connection.autocommit = False
                connection.read_only = True
                with connection.transaction(force_rollback=True):
                    yield connection
        except psycopg.Error as exc:
            raise DatabaseError(safe_error(exc)) from None
