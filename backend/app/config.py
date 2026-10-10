"""Configuración central del backend de Glazed Co. leída desde variables de entorno."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfiguracionAplicacion:
    """Agrupa los parámetros de entorno: conexión a Tiger Data, clave de Gemini y modo de datos."""

    tiger_data_url: str
    gemini_api_key: str
    usar_datos_simulados: bool


def cargar_configuracion_aplicacion() -> ConfiguracionAplicacion:
    """Lee las variables de entorno y construye la configuración.

    Parámetros: ninguno.
    Retorna: ConfiguracionAplicacion con valores por defecto seguros para desarrollo.
    """
    return ConfiguracionAplicacion(
        tiger_data_url=os.getenv("TIGER_DATA_URL", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        usar_datos_simulados=os.getenv("USAR_DATOS_SIMULADOS", "true").lower() == "true",
    )


configuracion_aplicacion = cargar_configuracion_aplicacion()