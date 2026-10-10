"""TrendsAPI REST documentado; solo ranking, sin asumir regiones ni métricas."""

from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import math
from threading import Lock
import time

import httpx

from .cache import LocalCache
from .config import ENDPOINT, SocialConfig
from .models import Hashtag, HashtagResult, SocialError, SocialFailure


def normalize(payload: object, queried_at: datetime, feed: str) -> list[Hashtag]:
    if not isinstance(payload, dict) or payload.get("type") != feed or not isinstance(payload.get("data"), list):
        raise SocialFailure("RESPUESTA_INVALIDA", "TrendsAPI devolvió un formato no reconocido.")
    result: list[Hashtag] = []
    seen: set[str] = set()
    for row in payload["data"]:
        # Único contrato documentado del feed: [rank, label]. No adivinar columnas adicionales.
        if (not isinstance(row, list) or len(row) < 2 or type(row[0]) is not int
                or row[0] < 1 or not isinstance(row[1], str) or not row[1].strip().lstrip("#")):
            raise SocialFailure("RESPUESTA_INVALIDA", "TrendsAPI devolvió un ranking no reconocido.")
        label = row[1].strip().lstrip("#")
        if any(character.isspace() for character in label):
            raise SocialFailure("RESPUESTA_INVALIDA", "TrendsAPI devolvió una etiqueta de hashtag no reconocida.")
        name = "#" + label
        if name.casefold() not in seen:
            result.append(Hashtag(hashtag=name, fecha_consulta=queried_at))
            seen.add(name.casefold())
    return result


class TrendsClient:
    def __init__(self, config: SocialConfig, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config, self.transport, self.clock, self.sleep = config, transport, clock, sleep
        self._lock = Lock()

    def get_trending_hashtags(self, categoria: str | None = None, palabra_clave: str | None = None,
                             region: str | None = None, limite: int = 10) -> HashtagResult:
        try:
            if not self.config.api_key:
                raise SocialFailure("LLAVE_AUSENTE", "Falta TRENDSAPI_KEY en el entorno.")
            if region is not None:
                raise SocialFailure("REGION_NO_VERIFICADA", "TrendsAPI no documenta un filtro regional para este feed; omite región.")
            if (type(limite) is not int or not 1 <= limite <= 200
                    or any(value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 100)
                           for value in (categoria, palabra_clave))):
                raise SocialFailure("PARAMETROS_INVALIDOS", "Usa filtros no vacíos y un límite entero entre uno y doscientos.")
            feed = "TikTok Trending Hashtags by Category" if categoria else "TikTok Trending Hashtags"
            body = {"mode": "get_top_trends", "type": feed, "limit": 200 if palabra_clave else limite}
            if categoria:
                body["category"] = categoria.strip()
            account = sha256(self.config.api_key.encode()).hexdigest()
            key = sha256((account + json.dumps(body, sort_keys=True)).encode()).hexdigest()
            with self._lock:
                cache = LocalCache(self.config.cache_path)
                hit = cache.get(key, self.clock())
                if hit is not None:
                    result = HashtagResult.model_validate_json(hit)
                    result.cache = True
                else:
                    payload = self._fetch(body, cache, account)
                    now = self.clock()
                    result = HashtagResult(items=normalize(payload, datetime.fromtimestamp(now, timezone.utc), feed))
                    cache.put(key, result.model_dump_json(), now + self.config.cache_ttl_seconds)
            if palabra_clave:
                term = palabra_clave.strip().lstrip("#").casefold()
                result.items = [item for item in result.items if term in item.hashtag.casefold()]
            result.items = result.items[:limite]
            return result
        except SocialFailure as exc:
            return HashtagResult(error=SocialError(codigo=exc.code, mensaje=exc.message))
        except Exception:
            return HashtagResult(error=SocialError(codigo="ERROR_SOCIAL", mensaje="No se pudo completar la consulta social."))

    def _fetch(self, body: dict, cache: LocalCache, account: str) -> object:
        cfg = self.config
        with httpx.Client(timeout=cfg.timeout_seconds, transport=self.transport, follow_redirects=False) as http:
            for attempt in range(cfg.retries + 1):
                cache.reserve(account, self.clock(), cfg.monthly_attempt_budget, cfg.min_interval_seconds)
                wait = max(cfg.min_interval_seconds, 2 ** attempt)
                code = 503
                try:
                    response = http.post(ENDPOINT, headers={"Authorization": f"Bearer {cfg.api_key}"}, json=body)
                    code = response.status_code
                    if 200 <= code < 300:
                        payload = response.json()
                        if isinstance(payload, dict) and "statusCode" in payload:
                            code = payload["statusCode"]
                            if type(code) is not int:
                                raise ValueError
                            payload = json.loads(payload["body"])
                        if 200 <= code < 300:
                            if isinstance(payload, dict) and "error" in payload:
                                raise SocialFailure("ERROR_PROVEEDOR", "TrendsAPI no pudo obtener los datos solicitados.")
                            return payload
                    header = response.headers.get("Retry-After")
                    if header:
                        try:
                            delay = float(header)
                        except ValueError:
                            delay = parsedate_to_datetime(header).timestamp() - self.clock()
                        if math.isfinite(delay):
                            wait = max(wait, delay)
                except httpx.RequestError:
                    pass
                except (ValueError, KeyError, TypeError):
                    raise SocialFailure("RESPUESTA_INVALIDA", "TrendsAPI devolvió una respuesta no reconocida.") from None
                if code == 429:
                    cache.defer(account, self.clock() + wait)
                if code not in (429, 500, 502, 503, 504):
                    raise SocialFailure("ERROR_PROVEEDOR", "TrendsAPI rechazó la solicitud; revisa acceso y parámetros.")
                if attempt == cfg.retries or wait > cfg.max_retry_wait_seconds:
                    raise SocialFailure("LIMITE_DE_USO" if code == 429 else "ERROR_RED",
                                        "TrendsAPI no está disponible o ha limitado las consultas; inténtalo más tarde.")
                self.sleep(wait)
        raise SocialFailure("ERROR_RED", "TrendsAPI no está disponible.")
