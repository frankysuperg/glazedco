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
                                Page, ReadyState, Replenishment, Store, Summary,
                                Answer, StrategyResponse)
from glazedco.api.errors import APIError
from test_api_enrichment import source_with_history


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
                     "/api/alertas", "/api/reabastecimiento", "/api/anomalias", "/api/sucursales",
                     "/api/asistente", "/api/tendencias/estrategias"}
    assert "300000" in html
    assert "Calculando por primera vez, puede tardar unos minutos" in html


class MockAssistant:
    async def answer(self, question, store):
        if question == "DROP TABLE sales_daily":
            raise APIError(422, "CONSULTA_INSEGURA", "No se pudo contestar la pregunta con seguridad.")
        return "Respuesta de prueba con datos verificados."


class MockStrategies:
    def __init__(self, data):
        self.data = data
        self.unavailable = False

    def generate(self, store):
        if self.unavailable:
            raise APIError(503, "TENDENCIAS_NO_DISPONIBLES", "Falta la llave de la fuente de tendencias.")
        return {**self.data.scope(store).model_dump(), "estrategias": [{
            "hecho": 0, "tipo": "paquete", "propuesta": "Probar un paquete de donas.",
            "efecto_margen": "Evaluar si protege el margen.", "hashtag": "#donas",
            "evidencias": ["/hashtag", "/margen/monto"],
            "evidencia_verificada": {"/hashtag": "#donas", "/margen/monto": 60}}],
            "hechos": {"hechos_tendencias": [{"hashtag": "#donas", "categoria": "donas"}]},
            "sin_relacion_con_catalogo": ["#sinmapa"], "estrategias_descartadas": 0,
            "aviso": "Señal externa como hipótesis; sin correlación demostrada."}


def dashboard_contracts():
    """Respuestas HTTP de prueba reutilizables para comprobación del navegador."""
    data = Dashboard(source_with_history())
    service = MockStrategies(data)
    snapshots = {}
    with ReadyClient(create_app(dashboard=data, assistant=MockAssistant(), strategy_service=service)) as client:
        for store in (None, "S1", "S2"):
            params = {"sucursal": store} if store else {}
            bodies = {}
            for path in ("estado", "dashboard/resumen", "margen", "pronostico/semanal", "alertas",
                         "reabastecimiento", "anomalias", "sucursales"):
                response = client.get("/api/" + path, params=params)
                assert response.status_code == 200
                bodies["/api/" + path] = response.json()
            for path, body, model in [("asistente", {"pregunta": "Resume el margen"}, Answer),
                                      ("tendencias/estrategias", {"objetivo": "Proponer paquetes"}, StrategyResponse)]:
                response = client.post("/api/" + path, params=params, json=body)
                assert response.status_code == 200
                assert model.model_validate(response.json()).sucursal == store
                bodies["/api/" + path] = response.json()
            assert client.post("/api/asistente", params=params, json={"pregunta": "DROP TABLE sales_daily"}).status_code == 422
            snapshots[store or ""] = bodies
        service.unavailable = True
        response = client.post("/api/tendencias/estrategias", json={"objetivo": "Proponer paquetes"})
        assert response.status_code == 503
        snapshots["unavailable"] = response.json()
    return snapshots


def test_complete_dashboard_contracts_include_new_fields_and_post_errors():
    snapshots = dashboard_contracts()
    assert snapshots["S1"]["/api/pronostico/semanal"]["comparacion"]["unidades_reales_4_semanas"] == 280
    assert snapshots["S2"]["/api/alertas"]["items"][0]["impacto"]["metodo"]
    assert snapshots[""]["/api/anomalias"]["items"][0]["duracion_dias"] == 1
