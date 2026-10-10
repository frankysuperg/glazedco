"""FastAPI: rutas de lectura y adaptadores de los módulos existentes."""

from typing import Annotated

from anyio import to_thread
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .assistant import Assistant, LiveAssistant
from .data import Dashboard, records
from .errors import APIError
from .models import (
    Alert, Anomaly, Answer, ErrorDetail, ErrorResponse, ForecastResponse, MarginResponse,
    Page, Question, Replenishment, Scope, Store, StrategyRequest, StrategyResponse, Summary,
)

Branch = Annotated[str | None, Query(min_length=1, max_length=100, description="Omitir = Todas las sucursales")]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0)]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(
        error=ErrorDetail(codigo=code, mensaje=message)).model_dump())


def _page(items: list, scope: Scope, limit: int, offset: int) -> dict:
    return {**scope.model_dump(), "items": items[offset:offset + limit], "total": len(items),
            "limite": limit, "desplazamiento": offset}


def create_app(*, dashboard: Dashboard | None = None, assistant: Assistant | None = None) -> FastAPI:
    data = dashboard or Dashboard()
    assistant_service = assistant or LiveAssistant()
    app = FastAPI(title="Glazed Co. Dashboard API", version="0.1.0", debug=False,
                  responses={code: {"model": ErrorResponse} for code in (404, 422, 500, 503)})

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "SOLICITUD_INVALIDA", "Revisa los campos, el filtro y los límites de la solicitud.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return _error(exc.status_code, "ERROR_HTTP", "La ruta o el método solicitado no está disponible.")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return _error(500, "ERROR_INTERNO", "No se pudo completar la solicitud.")

    @app.get("/api/dashboard/resumen", response_model=Summary)
    def summary(sucursal: Branch = None):
        scope = data.scope(sucursal)
        return Summary(**scope.model_dump(), margen=data.margin(sucursal), pronostico=data.forecast(sucursal),
                       alertas_abiertas=len(data.alerts(sucursal)), fecha_corte_alertas=data.config.as_of_date)

    @app.get("/api/margen", response_model=MarginResponse)
    def margin(sucursal: Branch = None):
        return MarginResponse(**data.scope(sucursal).model_dump(), margen=data.margin(sucursal))

    @app.get("/api/pronostico/semanal", response_model=ForecastResponse)
    def forecast(sucursal: Branch = None):
        return data.forecast(sucursal)

    @app.get("/api/reabastecimiento", response_model=Page[Replenishment])
    def replenishment(sucursal: Branch = None, limite: Limit = 100, desplazamiento: Offset = 0):
        scope = data.scope(sucursal)
        return _page(records(data.recommendations(sucursal)), scope, limite, desplazamiento)

    @app.get("/api/anomalias", response_model=Page[Anomaly])
    def anomalies(sucursal: Branch = None, limite: Limit = 100, desplazamiento: Offset = 0):
        scope = data.scope(sucursal)
        return _page(records(data.select(data.report("anomalies"), sucursal)), scope, limite, desplazamiento)

    @app.get("/api/alertas", response_model=Page[Alert])
    def alerts(sucursal: Branch = None, limite: Limit = 100, desplazamiento: Offset = 0):
        scope = data.scope(sucursal)
        return _page(data.alerts(sucursal), scope, limite, desplazamiento)

    @app.get("/api/sucursales", response_model=Page[Store])
    def stores(sucursal: Branch = None, q: Annotated[str, Query(max_length=100)] = "",
               limite: Limit = 100, desplazamiento: Offset = 0):
        scope = data.scope(sucursal)
        frame = data.select(data.tables()["stores"], sucursal)
        if q.strip():
            term = q.strip().casefold()
            frame = frame.loc[frame.store_name.str.casefold().str.contains(term, regex=False, na=False)
                              | frame.store_id.str.casefold().str.contains(term, regex=False, na=False)]
        return _page(records(frame[["store_id", "store_name"]].sort_values("store_id")), scope, limite, desplazamiento)

    @app.post("/api/asistente", response_model=Answer)
    async def ask(body: Question, sucursal: Branch = None):
        scope = await to_thread.run_sync(data.scope, sucursal)
        answer = await assistant_service.answer(body.pregunta, sucursal)
        return Answer(**scope.model_dump(), respuesta=answer)

    @app.post("/api/tendencias/estrategias", response_model=StrategyResponse)
    async def strategies(body: StrategyRequest, sucursal: Branch = None):
        await to_thread.run_sync(data.scope, sucursal)
        raise APIError(503, "TENDENCIAS_NO_DISPONIBLES", "Fuente de tendencias no disponible.")

    return app


app = create_app()
