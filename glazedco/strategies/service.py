"""Orquesta proveedores inyectables; solo el JSON determinista llega a Gemini."""

import json
import time
from contextlib import nullcontext
from typing import Protocol

from glazedco.api.data import Dashboard
from glazedco.api.errors import APIError
from glazedco.gemini import GeminiClient
from glazedco.social.client import TrendsClient
from glazedco.social.config import load_config as social_config
from glazedco.social.models import HashtagResult
from .config import StrategyConfig, load_config
from .facts import build_facts
from .validation import Discard, ProposalBatch, Strategy, validate_strategies
from . import validation


def demo_notice() -> str:
    return " Propuestas sin validación estricta (modo demo)" if not validation.STRICT_VALIDATION else ""


class Trends(Protocol):
    def get_trending_hashtags(self) -> HashtagResult: ...


class Creative(Protocol):
    def generate(self, contents: str, *, profile: str,
                 response_schema: dict[str, object] | None = None) -> str: ...


class StrategyService:
    def __init__(self, data: Dashboard, *, trends: Trends | None = None,
                 gemini: Creative | None = None, config: StrategyConfig | None = None):
        self.data, self.trends, self.gemini, self.config = data, trends, gemini, config

    def _fallback(self, payload: dict) -> list[Strategy]:
        """Ideas de campaña, sin inventar métricas, interés ni productos."""
        proposals: list[Strategy] = []
        seen: set[str] = set()
        try:
            products = self.data.tables()["products"]
        except Exception:
            products = None
        for index, fact in enumerate(payload["hechos_tendencias"]):
            tag = fact["hashtag"]
            if tag in seen:
                continue
            seen.add(tag)
            category = fact["categoria"]
            product = fact.get("product_name")
            if not product and products is not None and "product_name" in products.columns:
                names = products.loc[products.category.eq(category), "product_name"].dropna()
                if not names.empty:
                    product = str(names.iloc[0])
            subject = product or f"productos de la categoría {category}"
            prefix = f"/hechos_tendencias/{index}"
            evidence = {f"{prefix}/hashtag": tag, f"{prefix}/categoria": category}
            for name in ("margen_bruto_monto", "margen_bruto_porcentaje"):
                value = fact.get("margen", {}).get(name)
                if value is not None:
                    evidence[f"{prefix}/margen/{name}"] = value
            proposals.append(Strategy(
                hecho=index, tipo="promocion", hashtag=tag, origen="respaldo",
                propuesta=f"Proponer una campaña temática de {subject} vinculada a {tag}. "
                          "Canal sugerido: publicaciones en Instagram y exhibición en sucursal. "
                          "Evaluar interés con una prueba de campaña antes de ampliar.",
                efecto_margen="Podría favorecer la venta sin descuentos generales; revisar el margen "
                              "observado y la disponibilidad antes de ejecutar. No se garantiza impacto.",
                evidencias=list(evidence), evidencia_verificada=evidence))
            if len(proposals) == 5:
                break
        return proposals

    @staticmethod
    def _unavailable_trends(scope: dict) -> dict:
        return {**scope, "estrategias": [], "hechos": {"hechos_tendencias": [], "alcance": scope},
                "sin_relacion_con_catalogo": [], "estrategias_descartadas": 0, "descartes": [],
                "aviso": "Tendencias no disponibles; no se generaron propuestas por falta de hechos externos."
                         + demo_notice()}

    def generate(self, store: str | None = None) -> dict:
        scope = self.data.scope(store)
        try:
            trends = (self.trends or TrendsClient(social_config())).get_trending_hashtags()
        except Exception:
            return self._unavailable_trends(scope.model_dump())
        if trends.error:
            return self._unavailable_trends(scope.model_dump())
        try:
            payload, unmapped = build_facts(self.data, trends, self.config or load_config(), store)
        except APIError:
            raise
        except Exception:
            raise APIError(503, "EVIDENCIA_NO_DISPONIBLE", "No se pudo verificar el mapeo y los datos internos.") from None
        strategies: list[Strategy] = []
        rejected: list[Discard] = []
        if payload["hechos_tendencias"]:
            try:
                contents = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                response_schema = ProposalBatch.model_json_schema()
                target = min(5, len({fact["hashtag"] for fact in payload["hechos_tendencias"]}))
                context = nullcontext(self.gemini) if self.gemini is not None else GeminiClient()
                with context as client:
                    for attempt in range(2):
                        try:
                            response = client.generate(contents, profile="creative", response_schema=response_schema)
                        except Exception:
                            if strategies:
                                break
                            raise
                        proposed, discards = validate_strategies(response, payload)
                        if proposed:
                            strategies = proposed
                        rejected.extend(discards)
                        if len({strategy.hashtag for strategy in strategies}) >= target or attempt == 1:
                            break
                        time.sleep(8)
                        contents = json.dumps({**payload, "retroalimentacion": {
                            "codigos_rechazo": sorted({item.motivo for item in discards}),
                            "instruccion": "Regenera la lista completa: una propuesta por hashtag relacionado, máximo cinco."
                        }}, ensure_ascii=False, allow_nan=False)
            except Exception:
                pass  # La demo conserva propuestas previas o usa respaldo determinista.
            if not strategies:
                strategies = self._fallback(payload)
        return {**scope.model_dump(), "estrategias": strategies, "hechos": payload,
                "sin_relacion_con_catalogo": unmapped, "estrategias_descartadas": len(rejected),
                "descartes": rejected,
                "aviso": "Señal externa como hipótesis; sin correlación demostrada ni cambios al pronóstico. "
                         "Datos internos sintéticos del hackathon al corte indicado; no son datos actuales. "
                         "Hashtags no mapeados: sin relación con el catálogo." + demo_notice()}
