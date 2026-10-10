"""Contrato documentado con respuestas ficticias; HTTP MockTransport, nunca TrendsAPI real."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastmcp import Client
import httpx
import pytest

from glazedco.gemini import GeminiClient, GeminiConfig
from glazedco.social.client import TrendsClient
from glazedco.social.config import ENDPOINT, SocialConfig, load_config
from glazedco.sql_assistant.server import create_server


FEED = "TikTok Trending Hashtags"


def response(data=None, feed=FEED):
    return httpx.Response(200, json={"statusCode": 200, "body": json.dumps({
        "type": feed, "data": data if data is not None else [[1, "#donas"], [2, "cafe"]],
    })})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    loop = asyncio.new_event_loop()  # socketpair interno de Windows antes de bloquear conexiones.
    monkeypatch.setattr(asyncio.events, "new_event_loop", lambda: loop)
    def blocked(*args, **kwargs):
        raise AssertionError("Prohibido llamar servicios reales.")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield
    if not loop.is_closed():
        loop.close()


@pytest.fixture
def configured(tmp_path):
    return SocialConfig(api_key="fixture-secret", cache_path=tmp_path / "cache.sqlite3", cache_ttl_seconds=10)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, tzinfo=timezone.utc).timestamp()
        self.waits = []
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def make_client(config, handler, clock=None):
    clock = clock or Clock()
    return TrendsClient(config, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)


def test_documented_request_normalization_and_absent_metrics(configured):
    requests = []
    def handler(request):
        requests.append(request)
        return response([[1, "donas"], [2, "#DONAS"], [3, "#cafe"]])
    result = make_client(configured, handler).get_trending_hashtags(limite=2)
    assert result.error is None
    assert [x.hashtag for x in result.items] == ["#donas", "#cafe"]
    assert all(x.volumen is None and x.crecimiento is None for x in result.items)
    assert result.items[0].fecha_consulta.tzinfo is not None
    assert result.items[0].fuente == "https://trendsapi.ai/trends/tiktok-trends"
    assert str(requests[0].url) == ENDPOINT and requests[0].method == "POST"
    assert requests[0].headers["Authorization"] == "Bearer fixture-secret"
    assert json.loads(requests[0].content) == {"mode": "get_top_trends", "type": FEED, "limit": 2}
    assert requests[0].extensions["timeout"]["read"] == configured.timeout_seconds
    assert "fixture-secret" not in str(result) and "fixture-secret" not in repr(configured)


def test_category_and_local_keyword(configured):
    def handler(request):
        body = json.loads(request.content)
        assert body == {"mode": "get_top_trends", "type": FEED + " by Category",
                        "category": "fixture-category", "limit": 200}
        return response(feed=FEED + " by Category")
    result = make_client(configured, handler).get_trending_hashtags(categoria="fixture-category", palabra_clave="CAFE", limite=1)
    assert result.error is None and [h.hashtag for h in result.items] == ["#cafe"]


def test_persistent_cache_ttl_and_original_timestamp(configured):
    handler = MagicMock(side_effect=lambda r: response())
    clock = Clock()
    first = make_client(configured, handler, clock).get_trending_hashtags()
    clock.sleep(2)
    second = make_client(configured, handler, clock).get_trending_hashtags()
    assert second.cache and first.items == second.items and handler.call_count == 1
    clock.sleep(10)
    third = make_client(configured, handler, clock).get_trending_hashtags()
    assert not third.cache and handler.call_count == 2
    assert third.items[0].fecha_consulta > first.items[0].fecha_consulta
    assert b"fixture-secret" not in configured.cache_path.read_bytes()


def test_missing_key_no_http_or_cache(configured):
    handler = MagicMock()
    result = make_client(replace(configured, api_key=""), handler).get_trending_hashtags()
    assert result.error.codigo == "LLAVE_AUSENTE" and not result.items
    handler.assert_not_called()
    assert not configured.cache_path.exists()


def test_env_key(monkeypatch):
    monkeypatch.setattr("glazedco.social.config.load_environment", lambda: None)
    monkeypatch.setenv("TRENDSAPI_KEY", " fixture-key ")
    assert load_config().api_key == "fixture-key"


@pytest.mark.parametrize("kwargs,code", [({"region": "US"}, "REGION_NO_VERIFICADA"),
    ({"limite": 201}, "PARAMETROS_INVALIDOS"), ({"limite": True}, "PARAMETROS_INVALIDOS"),
    ({"categoria": " "}, "PARAMETROS_INVALIDOS")])
def test_unsupported_parameters_do_not_spend_calls(configured, kwargs, code):
    handler = MagicMock()
    assert make_client(configured, handler).get_trending_hashtags(**kwargs).error.codigo == code
    handler.assert_not_called()


def test_network_error_bounded_retries_and_no_secret(configured):
    clock = Clock()
    handler = MagicMock(side_effect=httpx.ConnectError("fixture-secret"))
    result = make_client(configured, handler, clock).get_trending_hashtags()
    assert result.error.codigo == "ERROR_RED" and not result.items
    assert handler.call_count == 3 and clock.waits == [1, 2]
    assert "fixture-secret" not in str(result)


def test_retry_after_and_envelope_429(configured):
    clock = Clock()
    handler = MagicMock(side_effect=[httpx.Response(200, headers={"Retry-After": "7"},
        json={"statusCode": 429, "body": '{"error":"limited"}'}), response()])
    result = make_client(configured, handler, clock).get_trending_hashtags()
    assert result.error is None and clock.waits == [7]


def test_long_retry_after_persists_cooldown(configured):
    clock = Clock()
    handler = MagicMock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    first = make_client(configured, handler, clock).get_trending_hashtags()
    second = make_client(configured, handler, clock).get_trending_hashtags()
    assert first.error.codigo == second.error.codigo == "LIMITE_DE_USO"
    assert handler.call_count == 1 and clock.waits == []


def test_retry_after_http_date(configured):
    from email.utils import format_datetime
    clock = Clock()
    retry_at = format_datetime(datetime.fromtimestamp(clock.now + 12, timezone.utc), usegmt=True)
    handler = MagicMock(side_effect=[httpx.Response(429, headers={"Retry-After": retry_at}), response()])
    result = make_client(configured, handler, clock).get_trending_hashtags()
    assert result.error is None and clock.waits == [12]


def test_direct_payload_and_empty_keyword_match(configured):
    client = make_client(configured, lambda r: httpx.Response(200, json={"type": FEED, "data": [[1, "donas"]]}))
    result = client.get_trending_hashtags(palabra_clave="cafe")
    assert result.error is None and result.items == []


def test_monthly_budget_and_local_throttle(configured):
    configured = replace(configured, monthly_attempt_budget=1)
    clock = Clock()
    handler = MagicMock(side_effect=lambda r: response())
    client = make_client(configured, handler, clock)
    assert client.get_trending_hashtags().error is None
    assert client.get_trending_hashtags(limite=9).error.codigo == "LIMITE_DE_USO"
    clock.sleep(2)
    assert client.get_trending_hashtags(limite=9).error.codigo == "PRESUPUESTO_AGOTADO"
    assert client.get_trending_hashtags().cache  # Caché no consume presupuesto.
    assert handler.call_count == 1


@pytest.mark.parametrize("reply,code", [
    (httpx.Response(200, json={"statusCode": 401, "body": '{"message":"secret"}'}), "ERROR_PROVEEDOR"),
    (httpx.Response(200, json={"statusCode": 200, "body": "not-json"}), "RESPUESTA_INVALIDA"),
    (response([[1, None]]), "RESPUESTA_INVALIDA"),
    (httpx.Response(200, json={"error": "no_data"}), "ERROR_PROVEEDOR"),
])
def test_provider_errors_are_explicit(configured, reply, code):
    handler = MagicMock(return_value=reply)
    result = make_client(configured, handler).get_trending_hashtags()
    assert result.error.codigo == code and not result.items and "secret" not in str(result)
    assert handler.call_count == 1


def test_mcp_registration_and_gemini_function_calling(configured):
    social = make_client(configured, lambda r: response())
    server = create_server(service=MagicMock(), social_client=social)
    sdk = MagicMock()
    # SDK simulado invoca la sesión real MCP recibida en tools, sin Gemini real.
    async def generate(**kwargs):
        config = kwargs["config"]
        assert config.temperature == 0
        assert config.automatic_function_calling.maximum_remote_calls == 3
        session = config.tools[0]
        tool_names = {t.name for t in (await session.list_tools()).tools}
        assert tool_names == {"get_schema", "run_readonly_sql", "get_trending_hashtags"}
        result = await session.call_tool("get_trending_hashtags", {"limite": 1})
        assert result.structuredContent["items"][0]["hashtag"] == "#donas"
        return SimpleNamespace(text="Hashtag devuelto: #donas; métricas no disponibles.")
    sdk.aio.models.generate_content = AsyncMock(side_effect=generate)
    sdk.aio.aclose = AsyncMock()
    gemini = GeminiClient(GeminiConfig(api_key="fake", model="fixture-model"), sdk_client=sdk)
    async def run():
        async with gemini, Client(server) as client:
            return await gemini.generate_with_mcp("Consulta hashtags", session=client.session)
    assert "#donas" in asyncio.run(run())
    sdk.aio.models.generate_content.assert_awaited_once()
    sdk.aio.aclose.assert_awaited_once()
    sdk.close.assert_called_once()
