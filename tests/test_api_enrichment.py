"""Campos adicionales y conservación de cifras existentes, con datos simulados."""

import pandas as pd
import pytest

from api_helpers import ReadyClient
from test_api import MemorySource, block_services  # noqa: F401
from glazedco.api.app import create_app
from glazedco.api.data import Dashboard
from glazedco.api.enrichment import EnrichmentConfig, enrich_anomalies
from glazedco.api.impact import ImpactEstimator
from glazedco.replenishment.config import load_config


def source_with_history():
    source = MemorySource()
    template = source.data["sales_daily"].iloc[0].to_dict()
    source.data["sales_daily"] = pd.DataFrame([
        {**template, "store_id": store, "date": day.date().isoformat(), "units_sold": units,
         "net_sales": units * price}
        for store, units, price in [("S1", 10., 10.), ("S2", 20., 20.)]
        for day in pd.date_range("2026-08-31", "2026-09-30")])
    source.reports["forecast"] = pd.DataFrame([
        {"store_id": store, "sku": "A", "semana": day.date().isoformat(), "horizonte": index,
         "yhat": units, "method": "moving_average"}
        for store, units in [("S1", 70.), ("S2", 140.)]
        for index, day in enumerate(pd.date_range("2026-10-05", periods=4, freq="7D"), 1)])
    return source


def test_four_full_weeks_partial_excluded_branch_filter_and_summary():
    source = source_with_history()
    # Esta fila está en la semana parcial y no debe afectar la comparación.
    source.data["sales_daily"].loc[source.data["sales_daily"].date.eq("2026-09-30"), "units_sold"] = 9999
    with ReadyClient(create_app(dashboard=Dashboard(source))) as client:
        for store, expected in [(None, 840), ("S1", 280), ("S2", 560)]:
            params = {"sucursal": store} if store else {}
            body = client.get("/api/pronostico/semanal", params=params).json()
            extra = body["comparacion"]
            assert extra["unidades_reales_4_semanas"] == expected
            assert extra["unidades_pronosticadas_4_semanas"] == expected
            assert extra["variacion_porcentual"] == 0
            assert extra["semanas_anteriores"][0]["semana"] == "2026-08-31"
            assert extra["semanas_anteriores"][-1]["fin"] == "2026-09-27"
            assert all(row["dias_con_registros"] == 7 for row in extra["semanas_anteriores"])
            summary = client.get("/api/dashboard/resumen", params=params).json()
            assert summary["pronostico"] == body


@pytest.mark.parametrize("mode", ["zero", "missing_week", "missing_units", "missing_forecast"])
def test_comparison_unavailable_and_zero_denominator(mode):
    source = source_with_history()
    if mode == "zero":
        source.data["sales_daily"]["units_sold"] = 0
    elif mode == "missing_week":
        source.data["sales_daily"] = source.data["sales_daily"].loc[source.data["sales_daily"].date.ge("2026-09-07")]
    elif mode == "missing_units":
        source.data["sales_daily"].loc[0, "units_sold"] = float("nan")
    else:
        source.reports["forecast"] = source.reports["forecast"].loc[source.reports["forecast"].horizonte.lt(4)]
    result = Dashboard(source).forecast(None).comparacion
    assert result.variacion_porcentual is None and result.motivo
    if mode == "zero":
        assert result.unidades_reales_4_semanas == 0
    if mode in {"missing_week", "missing_units"}:
        assert result.unidades_reales_4_semanas is None


def estimator(source, config=None):
    return ImpactEstimator(source.tables(), source.report("forecast"), config or load_config(), EnrichmentConfig())


def test_variation_uses_observed_denominator():
    source = source_with_history()
    source.reports["forecast"].loc[source.reports["forecast"].store_id.eq("S1"), "yhat"] = 105
    data = Dashboard(source)
    assert data.forecast("S1").comparacion.variacion_porcentual == 50
    assert data.forecast("S2").comparacion.variacion_porcentual == 0


def test_null_impacts_fall_back_to_severity():
    source = source_with_history()
    source.data["products"]["unit_cost"] = float("nan")
    data = Dashboard(source)
    recommendations = data.recommendations(None).copy()
    recommendations["riesgo_exceso"] = "bajo"
    recommendations["riesgo_quiebre"] = recommendations.store_id.map({"S1": "medio", "S2": "alto"})
    data._recommendations[None] = recommendations
    alerts = data.alerts(None)
    assert [alert.severidad for alert in alerts] == ["alta", "media"]
    assert all(alert.impacto.impacto_usd_estimado is None for alert in alerts)


def test_impact_formulas_and_branch_specific_prices():
    source = source_with_history()
    source.data["inventory_daily"]["closing_qty"] = 0
    calc = estimator(source)
    first = calc.estimate("S1", "A", "quiebre_stock")
    second = calc.estimate("S2", "A", "quiebre_stock")
    assert first.impacto_usd_estimado == 70 * (10 - 2)
    assert second.impacto_usd_estimado == 140 * (20 - 2)
    assert first.dias_en_riesgo == 7 and first.estimacion is True
    source.data["inventory_daily"].loc[1, "closing_qty"] = 2000
    excess = estimator(source).estimate("S2", "A", "sobreinventario")
    assert excess.unidades_exceso == 2000 - 14 * 20
    assert excess.impacto_usd_estimado == (2000 - 280) * 2
    assert "Capital inmovilizado" in excess.metodo


@pytest.mark.parametrize("missing", ["cost", "inventory", "forecast", "price", "lead", "horizon"])
def test_missing_inputs_produce_null_impact_and_reason(missing):
    source = source_with_history()
    if missing == "cost":
        source.data["products"]["unit_cost"] = float("nan")
    elif missing == "inventory":
        source.data["inventory_daily"].loc[0, "on_order_qty"] = float("nan")
    elif missing == "forecast":
        source.reports["forecast"].loc[0, "yhat"] = float("nan")
    elif missing == "price":
        source.data["sales_daily"] = source.data["sales_daily"].loc[source.data["sales_daily"].store_id.eq("S2")]
    elif missing == "lead":
        source.data["purchase_orders"] = source.data["purchase_orders"].iloc[:0]
        source.data["suppliers"]["standard_lead_time_days"] = float("nan")
    else:
        source.reports["forecast"] = source.reports["forecast"].iloc[:0]
    impact = estimator(source).estimate("S1", "A", "quiebre_stock")
    assert impact.impacto_usd_estimado is None and impact.motivo and impact.estimacion


def test_risk_days_fraction_perishability_and_no_forecast_extrapolation():
    source = source_with_history()
    source.data["inventory_daily"].loc[0, "closing_qty"] = 25
    impact = estimator(source).estimate("S1", "A", "quiebre_stock")
    assert impact.dias_en_riesgo == 4.5 and impact.unidades_en_riesgo == 45
    source.data["products"]["is_perishable"] = 1
    source.data["products"]["shelf_life_days"] = 2
    impact = estimator(source).estimate("S2", "A", "sobreinventario")
    assert impact.unidades_exceso == 2000 - 2 * 20
    source.data["purchase_orders"] = source.data["purchase_orders"].iloc[:0]
    source.data["suppliers"]["standard_lead_time_days"] = 90
    assert estimator(source).estimate("S1", "A", "quiebre_stock").impacto_usd_estimado is None


def test_alerts_sort_by_known_impact_before_pagination_and_scope():
    source = source_with_history()
    data = Dashboard(source)
    baseline = data.recommendations(None)
    with ReadyClient(create_app(dashboard=data)) as client:
        all_alerts = client.get("/api/alertas").json()["items"]
        assert all_alerts[0]["tipo"] == "sobreinventario"
        values = [a["impacto"]["impacto_usd_estimado"] for a in all_alerts]
        assert values == sorted(values, reverse=True)
        first = client.get("/api/alertas?limite=1").json()["items"][0]
        assert first == all_alerts[0]
        for store in ("S1", "S2"):
            selected = client.get("/api/alertas", params={"sucursal": store}).json()["items"]
            assert selected == [a for a in all_alerts if a["store_id"] == store]
        assert client.get("/api/dashboard/resumen").json()["alertas_abiertas"] == len(all_alerts)
    pd.testing.assert_frame_equal(data.recommendations(None), baseline)


def test_missing_cost_http_is_null_without_removing_alert():
    source = source_with_history()
    source.data["products"]["unit_cost"] = float("nan")
    with ReadyClient(create_app(dashboard=Dashboard(source))) as client:
        response = client.get("/api/alertas?sucursal=S1")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["severidad"] == "alta" and item["motivo"]
        assert item["impacto"]["impacto_usd_estimado"] is None
        assert "unit_cost" in item["impacto"]["motivo"]


def test_anomaly_thresholds_and_streaks_do_not_bridge_gaps_or_stores():
    source = MemorySource()
    template = source.reports["anomalies"].iloc[0].to_dict()
    rows = [{**template, "fecha": date, "store_id": store, "puntaje": score}
            for date, store, score in [("2026-09-01", "S1", -5.), ("2026-09-02", "S1", 3.),
                                       ("2026-09-02", "S1", 2.), ("2026-09-04", "S1", None),
                                       ("2026-09-03", "S2", 6.)]]
    source.reports["anomalies"] = pd.DataFrame(rows)
    data = Dashboard(source, enrichment=EnrichmentConfig(anomaly_medium=3, anomaly_high=5))
    with ReadyClient(create_app(dashboard=data)) as client:
        result = client.get("/api/anomalias?sucursal=S1").json()["items"]
        assert [r["severidad"] for r in result] == ["alta", "media", "baja", None]
        assert [r["duracion_dias"] for r in result] == [2, 2, 2, 1]
        assert result[-1]["motivo_severidad"]
        assert client.get("/api/anomalias?sucursal=S2").json()["items"][0]["duracion_dias"] == 1
        assert [{key: row[key] for key in template} for row in result] == rows[:4]
    changed = enrich_anomalies(source.report("anomalies"), EnrichmentConfig(anomaly_medium=6, anomaly_high=8))
    assert changed.severidad.iloc[0] == "baja"
