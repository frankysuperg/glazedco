"""Posvalidación por estrategia: referencias, cifras y hashtags verificables."""

from decimal import Decimal
import json
import logging
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Aceptar decimales sin separador de miles; exponentes se comparan por valor.
NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
HASHTAG = re.compile(r"#[\w]+", re.UNICODE)
logger = logging.getLogger(__name__)

RejectCode = Literal["esquema_invalido", "referencia_inexistente", "referencia_nula",
                     "hashtag_no_citado", "margen_no_citado", "cifra_sin_origen",
                     "cifra_en_efecto", "hashtag_ajeno"]


class Discard(BaseModel):
    indice: int | None
    motivo: RejectCode


class Rejected(ValueError):
    def __init__(self, code: RejectCode):
        self.code = code


def discard(index: int | None, code: RejectCode) -> Discard:
    # Solo códigos controlados; nunca contenido ni excepciones del proveedor.
    logger.warning("%s", code)
    return Discard(indice=index, motivo=code)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hecho: int = Field(ge=0)
    tipo: Literal["promocion", "paquete", "preposicionamiento"]
    propuesta: str = Field(min_length=1, max_length=2000)
    efecto_margen: str = Field(min_length=1, max_length=1000)
    evidencias: list[str] = Field(min_length=2, max_length=20)


class Strategy(Proposal):
    hashtag: str
    evidencia_verificada: dict[str, str | float | int | bool | None]
    caracter: Literal["Propuesta: efecto hipotético, sin impacto cuantificado"] = (
        "Propuesta: efecto hipotético, sin impacto cuantificado")


def leaves(value: object, path: str = "") -> dict:
    if isinstance(value, dict):
        return {key: leaf for name, child in value.items()
                for key, leaf in leaves(child, path + "/" + name.replace("~", "~0").replace("/", "~1")).items()}
    if isinstance(value, list):
        return {key: leaf for index, child in enumerate(value)
                for key, leaf in leaves(child, path + "/" + str(index)).items()}
    return {path: value}


def numbers(text: str) -> set[Decimal]:
    return {Decimal(token.replace(",", ".")) for token in NUMBER.findall(unicodedata.normalize("NFKC", text))}


def validate_strategies(response: str, payload: dict) -> tuple[list[Strategy], list[Discard]]:
    try:
        raw = json.loads(response)
        if not isinstance(raw, dict) or set(raw) != {"estrategias"} or not isinstance(raw["estrategias"], list):
            raise ValueError
        if len(raw["estrategias"]) > 20:
            raise ValueError
    except (ValueError, TypeError):
        return [], [discard(None, "esquema_invalido")]
    accepted: list[Strategy] = []
    rejected: list[Discard] = []
    for index, item in enumerate(raw["estrategias"]):
        try:
            proposal = Proposal.model_validate(item)
            if proposal.hecho >= len(payload["hechos_tendencias"]):
                raise Rejected("referencia_inexistente")
            fact = payload["hechos_tendencias"][proposal.hecho]
            fields = leaves(fact)
            prefix = f"/hechos_tendencias/{proposal.hecho}"
            refs = [ref[len(prefix):] if ref.startswith(prefix + "/") else ref
                    for ref in proposal.evidencias]
            if any(ref not in fields for ref in refs):
                raise Rejected("referencia_inexistente")
            if any(fields[ref] is None for ref in refs):
                raise Rejected("referencia_nula")
            if "/hashtag" not in refs:
                raise Rejected("hashtag_no_citado")
            if not any(ref.startswith("/margen/") for ref in refs):
                raise Rejected("margen_no_citado")
            text = unicodedata.normalize("NFKC", proposal.propuesta + " " + proposal.efecto_margen)
            # Nunca tomar índices de referencias, datos de usuario o claves como cifras de evidencia.
            if set(HASHTAG.findall(text)) - {fact["hashtag"]}:
                raise Rejected("hashtag_ajeno")
            numeric_text = text.replace(fact["hashtag"], "")
            # Una fecha completa puede citarse; su año no se convierte en un monto permitido.
            for value in fields.values():
                if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
                    numeric_text = numeric_text.replace(value, "")
            allowed = {Decimal(str(fields[ref])) for ref in refs if type(fields[ref]) in (int, float)}
            if numbers(proposal.efecto_margen):
                raise Rejected("cifra_en_efecto")
            if not numbers(numeric_text).issubset(allowed):
                raise Rejected("cifra_sin_origen")
            accepted.append(Strategy(**proposal.model_dump(), hashtag=fact["hashtag"],
                                     evidencia_verificada={original: fields[ref]
                                                           for original, ref in zip(proposal.evidencias, refs)}))
        except Rejected as exc:
            rejected.append(discard(index, exc.code))
        except (ValidationError, ValueError, TypeError, IndexError, KeyError):
            rejected.append(discard(index, "esquema_invalido"))
    return accepted, rejected
