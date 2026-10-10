"""Contratos públicos del dashboard, sin configuración ni credenciales."""

from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Scope(Model):
    sucursal: str | None = None
    vista: str = "Todas las sucursales"


class Margin(Model):
    ingresos_netos_total: float | None
    ingresos_base_margen: float | None
    cogs: float | None
    margen_bruto_monto: float | None
    margen_bruto_porcentaje: float | None
    margen_parcial: bool
    cobertura_ingresos_pct: float | None
    filas_totales: int
    filas_validas: int
    filas_excluidas: int
    filas_costo_estimado: int


class MarginResponse(Scope):
    moneda: Literal["USD"] = "USD"
    margen: Margin


class Week(Model):
    semana: date
    unidades: float | None
    series: int
    series_con_pronostico: int


class ForecastResponse(Scope):
    objetivo: str = "Unidades de ventas observadas; no demanda insatisfecha"
    semanas: list[Week]
    unidades_agregadas: float | None


Risk = Literal["alto", "medio", "bajo", "desconocido"]


class Replenishment(Model):
    store_id: str
    sku: str
    cantidad: int
    fecha_pedido: date
    riesgo_quiebre: Risk
    riesgo_exceso: Risk
    motivo: str
    stock_seguridad: float | None
    fuente_desviacion: str
    valores_por_defecto: bool


class Anomaly(Model):
    fecha: date
    entidad: str
    store_id: str
    sku: str | None
    metrica: str
    unidad: str
    valor_observado: float | None
    valor_esperado: float | None
    puntaje: float | None
    tipo: str
    cobertura_costo_pct: float | None
    causa_contextual: str


class Alert(Model):
    store_id: str
    sku: str
    tipo: Literal["quiebre_stock", "sobreinventario"]
    severidad: Literal["alta", "media", "desconocida"]
    estado: Literal["abierta"] = "abierta"
    fecha_corte: date
    motivo: str


class Store(Model):
    store_id: str
    store_name: str


T = TypeVar("T", bound=Model)


class Page(Scope, Generic[T]):
    items: list[T]
    total: int
    limite: int
    desplazamiento: int


class Summary(Scope):
    margen: Margin
    pronostico: ForecastResponse
    alertas_abiertas: int
    fecha_corte_alertas: date
    criterio_alertas: str = "Riesgos no bajos del corte; no existe historial de cierre"


class Question(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    pregunta: str = Field(min_length=1, max_length=4000)


class Answer(Scope):
    respuesta: str


class StrategyRequest(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    objetivo: str = Field(min_length=1, max_length=4000)


class StrategyResponse(Scope):
    estrategias: list[str]


class ErrorDetail(Model):
    codigo: str
    mensaje: str


class ErrorResponse(Model):
    error: ErrorDetail
