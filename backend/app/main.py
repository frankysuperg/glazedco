"""Punto de entrada de la API FastAPI de Glazed Co. (Tienda Inteligente para Retail)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import configuracion_aplicacion

aplicacion_glazed_co = FastAPI(
    title="Glazed Co. - Tienda Inteligente API",
    description="Backend de ML y estrategia comercial (Gemini) para Glazed Co.",
    version="0.1.0",
)

# Permite que el frontend de React (Vite, puerto 5173 por defecto) consuma la API en desarrollo.
aplicacion_glazed_co.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RespuestaEstadoServicio(BaseModel):
    """Esquema de respuesta del endpoint de salud."""

    estado: str
    usando_datos_simulados: bool
    gemini_configurado: bool


@aplicacion_glazed_co.get("/health", response_model=RespuestaEstadoServicio)
def consultar_estado_servicio() -> RespuestaEstadoServicio:
    """Verifica que la API está viva y muestra su modo de datos.

    Parámetros: ninguno.
    Retorna: RespuestaEstadoServicio con el estado, si usa datos simulados y si hay API key de Gemini.
    """
    return RespuestaEstadoServicio(
        estado="ok",
        usando_datos_simulados=configuracion_aplicacion.usar_datos_simulados,
        gemini_configurado=bool(configuracion_aplicacion.gemini_api_key),
    )