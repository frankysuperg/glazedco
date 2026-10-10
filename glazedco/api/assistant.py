"""Adaptador HTTP del flujo MCP existente con restricción de sucursal por AST."""

from contextlib import ExitStack
from typing import Protocol

from anyio import to_thread
from fastmcp import Client
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from glazedco.db import Database
from glazedco.gemini import GeminiClient
from glazedco.sql_assistant.assistant import UNSAFE, answer_question
from glazedco.sql_assistant.config import load_policy
from glazedco.sql_assistant.server import create_server
from glazedco.sql_assistant.service import QueryResult, SQLService
from glazedco.sql_validator import validate_sql
from .errors import APIError


class Assistant(Protocol):
    async def answer(self, question: str, store: str | None) -> str: ...


class StoreSQLService:
    """Solo tablas con store_id para filtros estrictos; nunca confiar en el prompt."""
    def __init__(self, service: SQLService, store: str) -> None:
        self.service, self.store = service, store

    def get_schema(self) -> dict[str, dict[str, str]]:
        return {name: columns for name, columns in self.service.get_schema().items() if "store_id" in columns}

    def run_readonly_sql(self, sql: str) -> QueryResult:
        try:
            validated = validate_sql(sql, self.get_schema(), policy=self.service.policy)
            query = parse_one(validated.sql, read="postgres")
            sources = {id(source): source for scope in traverse_scope(query)
                       for source in scope.sources.values() if isinstance(source, exp.Table)}
            if not sources:
                return QueryResult(status="rejected")
            for source in sources.values():
                base = source.copy()
                base.set("alias", None)
                filtered = exp.select("*").from_(base).where(
                    exp.EQ(this=exp.column("store_id"), expression=exp.Literal.string(self.store)))
                alias = source.args.get("alias") or exp.TableAlias(this=exp.to_identifier(source.name, quoted=True))
                source.replace(exp.Subquery(this=filtered, alias=alias.copy()))
            restricted = query.sql(dialect="postgres")
        except Exception:
            return QueryResult(status="rejected")
        # Revalida y ejecuta mediante el servicio original, nunca directamente.
        return self.service.run_readonly_sql(restricted)


class LiveAssistant:
    async def answer(self, question: str, store: str | None) -> str:
        with ExitStack() as stack:
            try:
                database = await to_thread.run_sync(lambda: stack.enter_context(Database(assistant=True)))
                service = await to_thread.run_sync(lambda: SQLService(database, load_policy()))
                gemini = await to_thread.run_sync(lambda: stack.enter_context(GeminiClient()))
            except Exception:
                raise APIError(503, "ASISTENTE_NO_DISPONIBLE", "No se pudo iniciar el asistente de consulta.") from None
            return await run_assistant(question, store, service=service, gemini=gemini)


async def run_assistant(question: str, store: str | None, *, service: SQLService, gemini: GeminiClient) -> str:
    scoped = StoreSQLService(service, store) if store is not None else service
    async with Client(create_server(service=scoped)) as client:
        result = await answer_question(question, gemini=gemini, client=client)
    if result == UNSAFE:
        raise APIError(422, "CONSULTA_RECHAZADA", "No se pudo contestar la pregunta con seguridad.")
    if result == "No se pudo ejecutar la consulta de forma segura.":
        raise APIError(503, "CONSULTA_NO_DISPONIBLE", "No se pudo ejecutar la consulta de forma segura.")
    return result
