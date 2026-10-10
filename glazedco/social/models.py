"""Salida normalizada: ausencia de métricas significa null, nunca cero."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Hashtag(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    hashtag: str
    volumen: float | None = None
    crecimiento: float | None = None
    fecha_consulta: datetime
    fuente: Literal["https://trendsapi.ai/trends/tiktok-trends"] = "https://trendsapi.ai/trends/tiktok-trends"


class SocialError(BaseModel):
    codigo: str
    mensaje: str


class HashtagResult(BaseModel):
    items: list[Hashtag] = Field(default_factory=list)
    error: SocialError | None = None
    cache: bool = False
    aviso: str = (
        "Volumen y crecimiento no están documentados en el ranking y se devuelven nulos. "
        "La palabra clave filtra localmente el ranking recibido; su cobertura depende del plan. "
        "El proveedor no documenta el alcance regional de este feed."
    )


class SocialFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)
