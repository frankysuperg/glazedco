"""Orquesta proveedores inyectables; solo el JSON determinista llega a Gemini."""

import json
from typing import Protocol

from glazedco.api.data import Dashboard
from glazedco.api.errors import APIError
from glazedco.gemini import GeminiClient
from glazedco.social.client import TrendsClient
from glazedco.social.config import load_config as social_config
from glazedco.social.models import HashtagResult
from .config import StrategyConfig, load_config
from .facts import build_facts
from .validation import validate_strategies


class Trends(Protocol):
    def get_trending_hashtags(self) -> HashtagResult: ...


class Creative(Protocol):
    def generate(self, contents: str, *, profile: str) -> str: ...


class StrategyService:
    def __init__(self, data: Dashboard, *, trends: Trends | None = None,
                 gemini: Creative | None = None, config: StrategyConfig | None = None):
        self.data, self.trends, self.gemini, self.config = data, trends, gemini, config

    def generate(self, store: str | None = None) -> dict:
        scope = self.data.scope(store)
        try:
            trends = (self.trends or TrendsClient(social_config())).get_trending_hashtags()
        except Exception:
            raise APIError(503, "TENDENCIAS_NO_DISPONIBLES", "No se pudo consultar la fuente de tendencias.") from None
        if trends.error:
            message = ("Falta la llave de la fuente de tendencias." if trends.error.codigo == "LLAVE_AUSENTE"
                       else "La fuente de tendencias no está disponible; revisa acceso o cuota.")
            raise APIError(503, "TENDENCIAS_NO_DISPONIBLES", message)
        try:
            payload, unmapped = build_facts(self.data, trends, self.config or load_config(), store)
        except APIError:
            raise
        except Exception:
            raise APIError(503, "EVIDENCIA_NO_DISPONIBLE", "No se pudo verificar el mapeo y los datos internos.") from None
        strategies, rejected = [], 0
        if payload["hechos_tendencias"]:
            try:
                contents = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                if self.gemini is not None:
                    response = self.gemini.generate(contents, profile="creative")
                else:
                    with GeminiClient() as client:
                        response = client.generate(contents, profile="creative")
                strategies, rejected = validate_strategies(response, payload)
            except Exception:
                raise APIError(503, "ESTRATEGIAS_NO_DISPONIBLES", "No se pudieron generar propuestas verificables.") from None
        return {**scope.model_dump(), "estrategias": strategies, "hechos": payload,
                "sin_relacion_con_catalogo": unmapped, "estrategias_descartadas": rejected,
                "aviso": "Señal externa como hipótesis; sin correlación demostrada ni cambios al pronóstico. "
                         "Datos internos sintéticos del hackathon al corte indicado; no son datos actuales. "
                         "Hashtags no mapeados: sin relación con el catálogo."}
