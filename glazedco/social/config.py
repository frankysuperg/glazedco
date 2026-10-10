"""Configuración social; los límites locales son conservadores, no cuotas del plan."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from glazedco.db import load_environment

ENDPOINT = "https://api.trendsapi.ai/api"


@dataclass(frozen=True)
class SocialConfig:
    api_key: str = field(repr=False)
    cache_path: Path = Path(__file__).resolve().parents[2] / "data" / "social" / "trends.sqlite3"
    timeout_seconds: float = 20
    retries: int = 2
    cache_ttl_seconds: int = 900
    min_interval_seconds: float = 1
    monthly_attempt_budget: int = 100
    max_retry_wait_seconds: float = 30

    def __post_init__(self) -> None:
        if (not 0 <= self.retries <= 2 or not 0 < self.timeout_seconds <= 60
                or self.cache_ttl_seconds <= 0 or self.min_interval_seconds <= 0
                or self.monthly_attempt_budget <= 0 or not 0 < self.max_retry_wait_seconds <= 30):
            raise ValueError("Configuración social inválida.")


def load_config() -> SocialConfig:
    load_environment()
    # TODO: Reemplazar con tus API Keys personales de TrendsAPI.
    return SocialConfig(api_key=os.getenv("TRENDSAPI_KEY", "").strip())
