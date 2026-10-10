"""Capa modular de Gemini sin lógica de negocio ni ejecución SQL."""

from .client import GeminiClient, GeminiError, explain_metrics
from .config import GeminiConfig, GeminiConfigError, load_config

__all__ = ["GeminiClient", "GeminiError", "GeminiConfig", "GeminiConfigError", "load_config", "explain_metrics"]
