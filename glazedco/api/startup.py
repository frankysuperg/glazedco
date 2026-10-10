"""Estado público del precalentamiento; ninguna excepción privada se publica."""

from dataclasses import dataclass
from time import perf_counter
import sys

from anyio import to_thread

from .data import Dashboard
from .errors import APIError
from .diagnostics import root_error_type, sanitized_traceback


@dataclass
class StartupState:
    listo: bool = False
    detalle: str = "Preparando datos y resultados."
    tipo_error: str | None = None

    async def prepare(self, data: Dashboard) -> None:
        self.tipo_error = None
        start = perf_counter()
        try:
            await to_thread.run_sync(data.warmup)
        except Exception as exc:
            self.tipo_error = root_error_type(exc)
            print(sanitized_traceback(exc), file=sys.stderr, flush=True)
            reasons = {
                "DATOS_NO_DISPONIBLES": "No se pudieron cargar, limpiar o guardar los datos; revisa base y caché local.",
                "REPORTE_NO_DISPONIBLE": "Falta un reporte publicado o no se pudo leer.",
                "RIESGO_INVALIDO": "El módulo devolvió un riesgo no reconocido.",
            }
            reason = "No se pudieron calcular los resultados internos."
            if isinstance(exc, APIError):
                reason = reasons.get(exc.code, reason)
            self.detalle = "Precalentamiento fallido. " + reason
        else:
            self.detalle = "Datos y resultados disponibles."
            self.listo = True
        finally:
            print(f"Precalentamiento {'completado' if self.listo else 'fallido'}: {perf_counter() - start:.2f} segundos.", flush=True)
