"""Configuración local de Gemini; nunca representa ni registra la llave."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv


class GeminiConfigError(ValueError):
    """Configuración incompleta, con mensaje apto para mostrar al usuario."""


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str = field(repr=False)
    model: str

    def __post_init__(self) -> None:
        for name, value in (("GEMINI_API_KEY", self.api_key), ("GEMINI_MODEL", self.model)):
            if not value or not value.strip():
                raise GeminiConfigError(f"Falta {name} en el entorno o en .env.")


def load_config() -> GeminiConfig:
    """Carga .env al inicializar, sin reemplazar variables del proceso."""
    try:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False, encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise GeminiConfigError("No se pudo cargar .env; revisa acceso y codificación UTF-8.") from None
    return GeminiConfig(api_key=os.getenv("GEMINI_API_KEY", "").strip(), model=os.getenv("GEMINI_MODEL", "").strip())
