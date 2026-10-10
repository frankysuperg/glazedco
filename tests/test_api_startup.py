"""Arranque no bloqueante, caché persistente y resultados invariantes, sin red."""

import json
import os
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from test_api import MemorySource, block_services  # noqa: F401
from api_helpers import ReadyClient
from glazedco.api.app import create_app
from glazedco.api.data import Dashboard, LocalSource
from glazedco.api import cache
from glazedco.cleaning.pipeline import TABLES


def wait_finished(client):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        state = client.get("/api/estado").json()
        if state["listo"] or "fallido" in state["detalle"]:
            return state
        sleep(0.01)
    pytest.fail("No terminó el precalentamiento")


def test_startup_is_background_and_all_routes_fail_fast_until_ready(capsys):
    data = Dashboard(MemorySource())
    warmup = data.warmup
    release = Event()
    def delayed():
        assert release.wait(20)
        warmup()
    data.warmup = delayed
    with TestClient(create_app(dashboard=data)) as client:
        try:
            assert client.get("/api/estado").json() == {
                "listo": False, "detalle": "Preparando datos y resultados."}
            for route in ("/api/dashboard/resumen", "/api/margen", "/api/pronostico/semanal",
                          "/api/reabastecimiento", "/api/anomalias", "/api/alertas", "/api/sucursales"):
                response = client.get(route)
                assert response.status_code == 503
                assert response.json()["reintentar_despues"] == 5
                assert response.headers["Retry-After"] == "5"
            for route in ("/api/asistente", "/api/tendencias/estrategias"):
                assert client.post(route, json={}).status_code == 503
        finally:
            release.set()
        assert wait_finished(client)["listo"] is True
        assert client.get("/api/dashboard/resumen").status_code == 200
    assert "Precalentamiento completado:" in capsys.readouterr().out


@pytest.mark.parametrize("failure,expected", [("tables", "datos"), ("report", "reporte"), ("warmup", "resultados internos")])
def test_failure_status_is_safe_and_console_has_duration(failure, expected, capsys):
    source = MemorySource()
    data = Dashboard(source)
    setattr(data if failure == "warmup" else source, failure,
            Mock(side_effect=RuntimeError("postgresql://user:SECRET@private?api_key=SECRET")))
    with TestClient(create_app(dashboard=data)) as client:
        state = wait_finished(client)
        assert state["listo"] is False and expected in state["detalle"]
        assert "SECRET" not in json.dumps(state) and "private" not in json.dumps(state)
        assert client.get("/api/dashboard/resumen").status_code == 503
    output = capsys.readouterr().out
    assert "Precalentamiento fallido:" in output and "SECRET" not in output


def test_warmup_caches_all_scopes_without_changing_results(monkeypatch):
    reference = Dashboard(MemorySource())
    data = Dashboard(MemorySource())
    expected = {store: (reference.margin(store), reference.forecast(store), reference.alerts(store),
                        reference.recommendations(store)) for store in (None, "S1", "S2")}
    with ReadyClient(create_app(dashboard=data)) as client:
        def unexpected(*args, **kwargs):
            raise AssertionError("No recalcular al atender la petición")
        monkeypatch.setattr("glazedco.api.data.aggregate_margin", unexpected)
        monkeypatch.setattr("glazedco.api.data.weekly_demand", unexpected)
        monkeypatch.setattr("glazedco.api.data.recommend_replenishment", unexpected)
        for store, (margin, forecast, alerts, recs) in expected.items():
            params = {"sucursal": store} if store else {}
            response = client.get("/api/dashboard/resumen", params=params)
            assert response.status_code == 200
            assert response.json()["margen"] == margin.model_dump(mode="json")
            assert response.json()["pronostico"] == forecast.model_dump(mode="json")
            assert response.json()["alertas_abiertas"] == len(alerts)
            pd.testing.assert_frame_equal(data.recommendations(store), recs)
        copy = data.margin(None)
        copy.margen_bruto_monto = -999
        assert data.margin(None) == expected[None][0]


def prepare_cache(tmp_path, monkeypatch):
    frame = pd.DataFrame({"sku": ["A"], "amount": [12.5]}, index=[7])
    tables = {name: frame.copy() for name in TABLES}
    db = Mock()
    db.return_value.__enter__ = Mock(return_value=object())
    db.return_value.__exit__ = Mock(return_value=False)
    read = Mock(return_value=tables)
    clean = Mock(return_value={name: (value, None, None) for name, value in tables.items()})
    monkeypatch.setattr(cache, "Database", db)
    monkeypatch.setattr(cache, "read_tables", read)
    monkeypatch.setattr(cache, "clean_all", clean)
    return LocalSource(tmp_path, cache_max_age_seconds=3600), tables, read, clean


def test_missing_cache_is_written_and_next_start_reuses_it(tmp_path, monkeypatch):
    source, tables, read, clean = prepare_cache(tmp_path, monkeypatch)
    first = source.tables()
    second = LocalSource(tmp_path, cache_max_age_seconds=3600).tables()
    read.assert_called_once()
    clean.assert_called_once()
    for name in TABLES:
        pd.testing.assert_frame_equal(first[name], tables[name])
        pd.testing.assert_frame_equal(second[name], tables[name])
        assert (tmp_path / "data" / "clean" / f"{name}.parquet").is_file()


@pytest.mark.parametrize("damage", ["old", "missing", "corrupt", "incomplete"])
def test_unusable_cache_rebuilds(tmp_path, monkeypatch, damage):
    source, _, read, clean = prepare_cache(tmp_path, monkeypatch)
    source.tables()
    path = tmp_path / "data" / "clean" / "sales_daily.parquet"
    if damage == "old":
        os.utime(path, (1, 1))
    elif damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"invalid parquet")
    else:
        (path.parent / ".api_cache.json").write_text('{"incompleta": true}')
    source.tables()
    assert read.call_count == 2 and clean.call_count == 2


@pytest.mark.parametrize("expired", [False, True])
def test_existing_cleaning_cache_respects_age_without_api_manifest(tmp_path, monkeypatch, expired):
    source, tables, read, _ = prepare_cache(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "clean"
    directory.mkdir(parents=True)
    for name, frame in tables.items():
        path = directory / f"{name}.parquet"
        frame.to_parquet(path)
        if expired:
            os.utime(path, (1, 1))
    loaded = source.tables()
    assert read.call_count == int(expired)
    pd.testing.assert_frame_equal(loaded["sales_daily"], tables["sales_daily"])


def test_max_age_environment_and_zero_forces_reload(tmp_path, monkeypatch):
    source, _, read, _ = prepare_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(cache, "load_environment", lambda: None)
    monkeypatch.setenv("API_CLEAN_CACHE_MAX_AGE_SECONDS", "0")
    assert cache.max_cache_age() == 0
    source.cache_max_age_seconds = None
    source.tables()
    source.tables()
    assert read.call_count == 2


def test_corrupt_legacy_cache_rebuilt(tmp_path, monkeypatch):
    source, tables, read, _ = prepare_cache(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "clean"
    directory.mkdir(parents=True)
    for name, frame in tables.items():
        frame.to_parquet(directory / f"{name}.parquet")
    (directory / "sales_daily.parquet").write_bytes(b"invalid parquet")
    source.tables()
    read.assert_called_once()
