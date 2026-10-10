"""HTML autocontenido y contratos HTTP reales con fuentes de prueba sin red."""

from html.parser import HTMLParser
from pathlib import Path
from threading import Event
import re

from fastapi.testclient import TestClient

from api_helpers import ReadyClient
from test_api import MemorySource, block_services  # noqa: F401
from glazedco.api.app import create_app
from glazedco.api.data import Dashboard
from glazedco.api.models import (Alert, Anomaly, ForecastResponse, MarginResponse,
                                Page, ReadyState, Replenishment, Store, Summary)


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []
    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"src", "href", "action"} and value and not value.startswith(("/", "#")):
                self.external.append(value)


def test_root_serves_html_during_warmup_and_has_no_external_urls():
    data = Dashboard(MemorySource())
    release = Event()
    original = data.warmup
    def warmup():
        assert release.wait(20)
        original()
    data.warmup = warmup
    with TestClient(create_app(dashboard=data)) as client:
        try:
            response = client.get("/")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert "Glazed Co." in response.text
            assert "Access-Control-Allow-Origin" not in response.headers
            assert not re.search(r"(?:https?:)?//[a-zA-Z0-9]", response.text)
            parsed = Resources()
            parsed.feed(response.text)
            assert parsed.external == []
            assert "<style>" in response.text and "<script>" in response.text
            assert "prefers-reduced-motion" in response.text
            assert client.get("/api/estado").json()["listo"] is False
            assert client.get("/api/dashboard/resumen").status_code == 503
        finally:
            release.set()


def test_each_consumed_endpoint_matches_pydantic_contract_and_scope():
    routes = {
        "/api/estado": ReadyState,
        "/api/dashboard/resumen": Summary,
        "/api/margen": MarginResponse,
        "/api/pronostico/semanal": ForecastResponse,
        "/api/alertas": Page[Alert],
        "/api/reabastecimiento": Page[Replenishment],
        "/api/anomalias": Page[Anomaly],
        "/api/sucursales": Page[Store],
    }
    with ReadyClient(create_app(dashboard=Dashboard(MemorySource()))) as client:
        assert client.get("/").status_code == 200
        for branch in (None, "S1"):
            for route, model in routes.items():
                response = client.get(route, params={"sucursal": branch} if branch else {})
                assert response.status_code == 200, route
                body = model.model_validate(response.json())
                if route != "/api/estado":
                    assert body.sucursal == branch
        stores = client.get("/api/sucursales?q=Centro").json()
        assert stores["items"] == [{"store_id": "S1", "store_name": "Centro"}]
        assert client.get("/api/sucursales?limite=1&desplazamiento=1").json()["items"][0]["store_id"] == "S2"


def test_page_uses_only_read_endpoints_and_no_extra_assets():
    html = (Path(__file__).resolve().parents[1] / "glazedco/api/static/index.html").read_text(encoding="utf-8")
    paths = set(re.findall(r"['\"](/api/[^'\"]+)['\"]", html))
    assert paths == {"/api/estado", "/api/dashboard/resumen", "/api/margen", "/api/pronostico/semanal",
                     "/api/alertas", "/api/reabastecimiento", "/api/anomalias", "/api/sucursales"}
    assert "300000" in html
    assert "Calculando por primera vez, puede tardar unos minutos" in html
