"""Estado público del precalentamiento; ninguna excepción privada se publica."""

from dataclasses import dataclass
from time import perf_counter

from anyio import to_thread

from .data import Dashboard
from .errors import APIError


@dataclass
class StartupState:
    listo: bool = False
    detalle: str = "Preparando datos y resultados."

    async def prepare(self, data: Dashboard) -> None:
        start = perf_counter()
        try:
            await to_thread.run_sync(data.warmup)
        except Exception as exc:
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
