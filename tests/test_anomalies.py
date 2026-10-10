"""Detección robusta, contexto verificable y ajuste sin fuga temporal."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from glazedco.anomalies import AnomalyConfig, detect_anomalies, load_config
from glazedco.anomalies.context import add_context
from glazedco.anomalies.detect import daily_metrics
from glazedco.anomalies.report import impact_table
from glazedco.forecast.anomalies import adjust_history
from glazedco.forecast.evaluate import evaluate_forecasts, forecast_future


def sales() -> pd.DataFrame:
    days = pd.date_range("2026-01-01", periods=140)
    units = np.where(days.dayofweek >= 5, 140.0, 100.0)
    return pd.DataFrame({"date": days, "store_id": "S1", "sku": "A", "units_sold": units,
                         "net_sales": units * 5, "unit_cost_aplicado": 2.0,
                         "costo_desconocido": False, "incluible_margen": True,
                         "costo_estimado": False, "apta_pronostico": True, "promo_id": None})


def panel() -> pd.DataFrame:
    frame = pd.DataFrame({"store_id": "S1", "sku": "A", "semana": pd.date_range("2025-10-06", periods=32, freq="W-MON"),
                          "units_sold": 10.0, "entrenable": True})
    frame.loc[[15, 25], "units_sold"] = 100
    return frame


def test_pico_y_caida_inyectados_y_entrada_intacta() -> None:
    frame = sales()
    frame.loc[[80, 110], "units_sold"] = [800, 1]
    frame["net_sales"] = frame.units_sold * 5
    original = frame.copy(deep=True)
    alerts = detect_anomalies(frame, load_config())
    units = alerts.loc[alerts.entidad.eq("producto") & alerts.metrica.eq("units_sold")]
    assert set(units.fecha) == {frame.loc[80, "date"], frame.loc[110, "date"]}
    assert set(units.tipo) == {"pico", "caída"}
    assert units.causa_contextual.eq("sin causa identificada").all()
    assert units.valor_esperado.between(100, 140).all()
    pd.testing.assert_frame_equal(frame, original)


def test_serie_normal_con_fin_de_semana_no_genera_falsos_positivos() -> None:
    assert detect_anomalies(sales(), load_config()).empty


def test_margen_monto_y_porcentaje_separados_y_costo_nulo_no_cero() -> None:
    frame = sales()
    frame.loc[80, "net_sales"] = frame.loc[80, "units_sold"] * 3
    frame.loc[100, ["unit_cost_aplicado", "costo_desconocido", "incluible_margen"]] = [np.nan, True, False]
    frame.loc[110, "net_sales"] = 0
    daily = daily_metrics(frame)
    unknown = daily.loc[daily.fecha.eq(frame.loc[100, "date"])]
    assert unknown.margen_bruto_monto.isna().all()
    assert daily.loc[daily.fecha.eq(frame.loc[110, "date"]), "margen_bruto_porcentaje"].isna().all()
    alerts = detect_anomalies(frame, load_config())
    margin = alerts.loc[alerts.fecha.eq(frame.loc[80, "date"]) & alerts.metrica.str.startswith("margen_")]
    assert set(margin.metrica) == {"margen_bruto_monto", "margen_bruto_porcentaje"}
    assert margin.tipo.eq("cambio de margen").all()
    assert set(margin.unidad) == {"USD", "%"}


def test_contexto_solo_eventos_presentes_y_en_su_entidad() -> None:
    frame = sales()
    day = frame.loc[80, "date"]
    frame.loc[80, "promo_id"] = "P1"
    tables = {
        "sales_daily": frame,
        "promotions": pd.DataFrame([{"promo_id": "P1", "start_date": day, "end_date": day}]),
        "inventory_daily": pd.DataFrame([{"date": day, "store_id": "S1", "sku": "A", "stockout_hours": 8}]),
        "calendar": pd.DataFrame([{"date": day, "is_holiday": 1, "holiday_name": "Fiesta", "is_school_break": 1}]),
        "store_closures": pd.DataFrame([{"store_id": "S1", "start_date": day, "end_date": day, "reason": "mantenimiento"}]),
    }
    alerts = pd.DataFrame([{"fecha": day, "entidad": "producto", "store_id": "S1", "sku": "A"},
                           {"fecha": day + pd.Timedelta(days=1), "entidad": "producto", "store_id": "S2", "sku": "B"}])
    result = add_context(alerts, tables)
    cause = result.iloc[0].causa_contextual
    for label in ("P1", "Fiesta", "vacaciones escolares", "cierre registrado: mantenimiento", "quiebre registrado"):
        assert label in cause
    assert result.iloc[1].causa_contextual == "sin causa identificada"
    assert "causa_contextual" not in alerts


def test_umbral_configurable_y_fechas_imputadas_fuera_de_deteccion() -> None:
    frame = sales()
    frame.loc[80, "net_sales"] = 100000
    assert not detect_anomalies(frame, load_config()).empty
    assert detect_anomalies(frame, replace(load_config(), threshold=1e8)).empty
    frame.loc[80, "apta_pronostico"] = False
    assert detect_anomalies(frame, load_config()).empty


def test_rechaza_tabla_cruda() -> None:
    with pytest.raises(ValueError, match="limpio"):
        detect_anomalies(sales().drop(columns="costo_desconocido"), load_config())


@pytest.mark.parametrize("action", ["conservar", "excluir", "suavizar"])
def test_ajuste_opera_en_copia_sin_rehabilitar_censura(action) -> None:
    original = panel()
    original.loc[20, ["units_sold", "entrenable"]] = [0, False]
    before = original.copy(deep=True)
    result = adjust_history(original, replace(load_config(), forecast_action=action))
    pd.testing.assert_frame_equal(original, before)
    assert not result.loc[20, "entrenable"]
    if action == "conservar":
        pd.testing.assert_frame_equal(result, original)
    elif action == "excluir":
        assert not result.loc[15, "entrenable"] and result.loc[15, "units_sold"] == 100
    else:
        assert result.loc[15, "units_sold"] == 10


@pytest.mark.parametrize("action", ["excluir", "suavizar"])
def test_validacion_conserva_verdad_y_no_filtra_anomalias_del_test(action) -> None:
    original = panel()
    config = replace(load_config(), forecast_action=action)
    baseline = evaluate_forecasts(original, horizon=4, n_splits=3)
    adjusted = evaluate_forecasts(original, horizon=4, n_splits=3, anomaly_config=config)
    keys = ["store_id", "sku", "semana", "horizonte", "model"]
    pd.testing.assert_series_equal(baseline.predictions.set_index(keys).y, adjusted.predictions.set_index(keys).y)
    assert adjusted.predictions.loc[adjusted.predictions.semana.eq(original.loc[25, "semana"]), "y"].eq(100).all()
    assert not impact_table(baseline, adjusted).empty
    future = forecast_future(original, adjusted)
    assert len(future) == 4


def test_futuro_no_cambia_ajuste_ni_pronostico_del_primer_origen() -> None:
    original = panel()
    config = replace(load_config(), forecast_action="suavizar")
    altered = original.copy()
    altered.loc[31, "units_sold"] = 100000
    pd.testing.assert_frame_equal(adjust_history(original, config).iloc[:31], adjust_history(altered, config).iloc[:31])
    first = evaluate_forecasts(original, horizon=4, n_splits=3, anomaly_config=config)
    second = evaluate_forecasts(altered, horizon=4, n_splits=3, anomaly_config=config)
    cutoff = original.loc[23, "semana"]
    pd.testing.assert_frame_equal(first.predictions.loc[first.predictions.semana.le(cutoff)], second.predictions.loc[second.predictions.semana.le(cutoff)])


def test_config_rechaza_tratamiento_desconocido() -> None:
    with pytest.raises(ValueError, match="forecast_action"):
        AnomalyConfig(forecast_action="borrar")


def test_conservar_reproduce_el_pronostico_sin_ajuste() -> None:
    original = panel()
    before = original.copy(deep=True)
    baseline = evaluate_forecasts(original, horizon=4, n_splits=3)
    kept = evaluate_forecasts(original, horizon=4, n_splits=3, anomaly_config=load_config())
    pd.testing.assert_frame_equal(baseline.predictions, kept.predictions)
    pd.testing.assert_frame_equal(baseline.metrics, kept.metrics)
    pd.testing.assert_frame_equal(original, before)


def test_historia_insuficiente_no_es_anomalia() -> None:
    frame = sales().iloc[:7].copy()
    frame.loc[6, "net_sales"] = 100000
    assert detect_anomalies(frame, load_config()).empty
