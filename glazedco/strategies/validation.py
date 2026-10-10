"""Posvalidación por estrategia: referencias, cifras y hashtags verificables."""

from decimal import Decimal
import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Aceptar decimales sin separador de miles; exponentes se comparan por valor.
NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
HASHTAG = re.compile(r"#[\w]+", re.UNICODE)


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


def validate_strategies(response: str, payload: dict) -> tuple[list[Strategy], int]:
    try:
        raw = json.loads(response)
        if not isinstance(raw, dict) or set(raw) != {"estrategias"} or not isinstance(raw["estrategias"], list):
            raise ValueError
        if len(raw["estrategias"]) > 20:
            raise ValueError
    except (ValueError, TypeError):
        return [], 1
    accepted: list[Strategy] = []
    rejected = 0
    for item in raw["estrategias"]:
        try:
            proposal = Proposal.model_validate(item)
            fact = payload["hechos_tendencias"][proposal.hecho]
            fields = leaves(fact)
            refs = proposal.evidencias
            if (any(ref not in fields or fields[ref] is None for ref in refs) or "/hashtag" not in refs
                    or not any(ref.startswith("/margen/") for ref in refs)):
                raise ValueError
            text = unicodedata.normalize("NFKC", proposal.propuesta + " " + proposal.efecto_margen)
            # Nunca tomar índices de referencias, datos de usuario o claves como cifras de evidencia.
            if set(HASHTAG.findall(text)) - {fact["hashtag"]}:
                raise ValueError
            numeric_text = text.replace(fact["hashtag"], "")
            # Una fecha completa puede citarse; su año no se convierte en un monto permitido.
            for value in fields.values():
                if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
                    numeric_text = numeric_text.replace(value, "")
            allowed = {Decimal(str(fields[ref])) for ref in refs if type(fields[ref]) in (int, float)}
            if not numbers(numeric_text).issubset(allowed) or numbers(proposal.efecto_margen):
                raise ValueError
            accepted.append(Strategy(**proposal.model_dump(), hashtag=fact["hashtag"],
                                     evidencia_verificada={ref: fields[ref] for ref in refs}))
        except (ValidationError, ValueError, TypeError, IndexError, KeyError):
            rejected += 1
    return accepted, rejected
