"""Mapeo explícito, local y validado contra el catálogo por el paso determinista."""

from pathlib import Path
import tomllib

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weeks: int = Field(default=4, ge=1, le=52)
    hashtags: dict[str, Mapping] = Field(default_factory=dict)

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, value: dict[str, Mapping]) -> dict[str, Mapping]:
        normalized = {key.strip().casefold(): mapping for key, mapping in value.items()}
        if len(normalized) != len(value) or any(not key.startswith("#") or len(key) < 2 for key in normalized):
            raise ValueError("Hashtags explícitos, únicos y con prefijo # requeridos")
        return normalized


def load_config(path: Path | None = None) -> StrategyConfig:
    with (path or Path(__file__).with_name("mapping.toml")).open("rb") as stream:
        return StrategyConfig.model_validate(tomllib.load(stream))
