"""Servidor FastMCP por stdio; catálogo y pool viven durante el lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import to_thread
from fastmcp import Context, FastMCP

from glazedco.db import Database
from glazedco.sql_validator import SQLPolicy
from glazedco.social.client import TrendsClient
from glazedco.social.tools import register_social_tools
from .config import load_policy
from .service import QueryResult, SQLService


def create_server(*, service: SQLService | None = None, policy: SQLPolicy | None = None,
                  social_client: TrendsClient | None = None) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, SQLService]]:
        if service is not None:
            yield {"service": service}
            return
        try:
            configured = policy or load_policy()
            with Database(assistant=True) as database:
                loaded = await to_thread.run_sync(SQLService, database, configured)
                yield {"service": loaded}
        except Exception:
            raise RuntimeError("No se pudo iniciar o mantener el servicio SQL seguro.") from None

    server = FastMCP("Glazed Co. SQL", lifespan=lifespan, mask_error_details=True)
    register_social_tools(server, social_client)

    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def get_schema(ctx: Context) -> dict[str, dict[str, str]]:
        """Devuelve solo las tablas autorizadas y sus columnas con tipos del catálogo."""
        return ctx.lifespan_context["service"].get_schema()

    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def run_readonly_sql(sql: str, ctx: Context) -> QueryResult:
        """Valida SELECT, limita filas y ejecuta con timeout y transacción de solo lectura."""
        return ctx.lifespan_context["service"].run_readonly_sql(sql)

    return server
