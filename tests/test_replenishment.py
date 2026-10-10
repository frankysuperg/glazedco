"""Pruebas de reabastecimiento con datos sintéticos (sin BD)."""

from datetime import date

import pandas as pd
import pytest

from glazedco.forecast.errors import series_forecast_errors
from glazedco.forecast.evaluate import EvalResult
from glazedco.forecast.select import Selection
from glazedco.replenishment.config import load_config
from glazedco.replenishment.demand import daily_rates_from_forecast
from glazedco.replenishment.recommend import recommend_replenishment


def _cfg(**overrides):
    return load_config(**overrides)


def _forecast(store="S1", sku="A", yhat=70.0) -> pd.DataFrame:
    # Semanas a partir de 2026-10-05 (lunes); yhat semanal → 10 u/día si yhat=70
    rows = []
    for h in range(1, 5):
        rows.append({
            "store_id": store, "sku": sku,
            "semana": (pd.Timestamp("2026-10-05") + pd.Timedelta(weeks=h - 1)).strftime("%Y-%m-%d"),
            "horizonte": h, "method": "moving_average", "yhat": yhat,
        })
    return pd.DataFrame(rows)


def _errors(store="S1", sku="A", n=12, desviacion=5.0, method="moving_average") -> pd.DataFrame:
    return pd.DataFrame([{
        "store_id": store, "sku": sku, "method": method,
        "n_residuos": n, "sesgo": 0.0, "desviacion": desviacion,
    }])


def _inventory(store="S1", sku="A", closing=0.0, on_order=0.0) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2026-09-30", "store_id": store, "sku": sku,
        "closing_qty": closing, "on_order_qty": on_order,
    }])


def _products(**overrides) -> pd.DataFrame:
    base = {
        "sku": "A", "supplier_id": "P1", "unit_cost": 2.0, "list_price_base": 5.0,
        "case_size": 6, "shelf_life_days": 2, "is_perishable": 0,
        "inventory_tracked": 1, "category": "donas",
    }
    base.update(overrides)
    return pd.DataFrame([base])


def _orders(lead_days=7) -> pd.DataFrame:
    return pd.DataFrame([{
        "po_id": "PO1", "store_id": "S1", "sku": "A", "supplier_id": "P1",
        "order_date": "2026-09-01", "received_date": f"2026-09-{1 + lead_days:02d}",
        "qty_ordered": 12.0, "qty_received": 12.0, "unit_cost": 2.0,
        "status": "Delivered", "valida_plazos_cumplimiento": True,
    }])


def _suppliers(lead=7) -> pd.DataFrame:
    return pd.DataFrame([{
        "supplier_id": "P1", "standard_lead_time_days": lead,
    }])


def test_quiebre_inminente_genera_pedido_y_riesgo_alto() -> None:
    config = _cfg(z=1.65, cobertura_objetivo_dias=14, presupuesto=1e9, min_residuos=4)
    result = recommend_replenishment(
        _forecast(yhat=70), _errors(), _inventory(closing=5, on_order=0),
        _products(is_perishable=0, shelf_life_days=30, case_size=1),
        _orders(7), _suppliers(7), config,
    )
    row = result.iloc[0]
    assert row.cantidad > 0
    assert row.riesgo_quiebre == "alto"
    assert row.fecha_pedido == "2026-10-01"
    assert row.store_id == "S1" and row.sku == "A"


def test_exceso_de_cobertura_marca_riesgo_y_no_pide() -> None:
    config = _cfg(cobertura_objetivo_dias=7, umbral_exceso_semanas=2, presupuesto=1e9, min_residuos=4)
    # 10 u/día; stock 500 → ~50 días ≈ 7.1 semanas > umbral 2
    result = recommend_replenishment(
        _forecast(yhat=70), _errors(desviacion=0.0), _inventory(closing=500, on_order=0),
        _products(is_perishable=0, shelf_life_days=60, case_size=1),
        _orders(7), _suppliers(7), config,
    )
    row = result.iloc[0]
    assert row.cantidad == 0
    assert row.riesgo_exceso == "alto"
    assert "Exceso" in row.motivo or "exceso" in row.motivo.lower() or "no pedir" in row.motivo.lower()


def test_cantidad_redondea_hacia_arriba_a_case_size() -> None:
    config = _cfg(z=0, cobertura_objetivo_dias=7, presupuesto=1e9, min_residuos=4, default_desviacion=0)
    # cobertura 7d × 10 u/día = 70; stock 0 → 70; case_size 12 → 72
    result = recommend_replenishment(
        _forecast(yhat=70), _errors(desviacion=0.0), _inventory(closing=0),
        _products(is_perishable=0, shelf_life_days=60, case_size=12),
        _orders(7), _suppliers(7), config,
    )
    assert result.iloc[0].cantidad == 72


def test_presupuesto_insuficiente_prioriza_margen_y_marca_fuera() -> None:
    forecast = pd.concat([_forecast(sku="A", yhat=70), _forecast(sku="B", yhat=70)], ignore_index=True)
    errors = pd.concat([_errors(sku="A"), _errors(sku="B")], ignore_index=True)
    inventory = pd.concat([_inventory(sku="A", closing=0), _inventory(sku="B", closing=0)], ignore_index=True)
    products = pd.DataFrame([
        {"sku": "A", "supplier_id": "P1", "unit_cost": 1.0, "list_price_base": 1.1,
         "case_size": 1, "shelf_life_days": 30, "is_perishable": 0, "inventory_tracked": 1, "category": "x"},
        {"sku": "B", "supplier_id": "P1", "unit_cost": 1.0, "list_price_base": 3.0,
         "case_size": 1, "shelf_life_days": 30, "is_perishable": 0, "inventory_tracked": 1, "category": "x"},
    ])
    # Cada pedido ≈ 70 u × $1; presupuesto 80 cubre solo el de mayor margen (B).
    config = _cfg(z=0, cobertura_objetivo_dias=7, presupuesto=80.0, min_residuos=4, default_desviacion=0)
    result = recommend_replenishment(
        forecast, errors, inventory, products, _orders(7), _suppliers(7), config,
    )
    by_sku = result.set_index("sku")
    assert by_sku.loc["B"].cantidad > 0
    assert by_sku.loc["A"].cantidad == 0
    assert "Fuera de presupuesto" in by_sku.loc["A"].motivo


def test_pocos_residuos_usa_desviacion_de_categoria() -> None:
    forecast = pd.concat([_forecast(sku="A", yhat=70), _forecast(sku="B", yhat=70)], ignore_index=True)
    errors = pd.DataFrame([
        {"store_id": "S1", "sku": "A", "method": "moving_average", "n_residuos": 2, "sesgo": 0.0, "desviacion": 1.0},
        {"store_id": "S1", "sku": "B", "method": "moving_average", "n_residuos": 20, "sesgo": 0.0, "desviacion": 14.0},
    ])
    inventory = pd.concat([_inventory(sku="A", closing=0), _inventory(sku="B", closing=1000)], ignore_index=True)
    products = pd.DataFrame([
        {"sku": "A", "supplier_id": "P1", "unit_cost": 2.0, "list_price_base": 5.0,
         "case_size": 1, "shelf_life_days": 30, "is_perishable": 0, "inventory_tracked": 1, "category": "donas"},
        {"sku": "B", "supplier_id": "P1", "unit_cost": 2.0, "list_price_base": 5.0,
         "case_size": 1, "shelf_life_days": 30, "is_perishable": 0, "inventory_tracked": 1, "category": "donas"},
    ])
    config = _cfg(z=1.65, cobertura_objetivo_dias=7, presupuesto=1e9, min_residuos=8)
    result = recommend_replenishment(
        forecast, errors, inventory, products, _orders(7), _suppliers(7), config,
    )
    row_a = result.loc[result.sku.eq("A")].iloc[0]
    assert "desviacion de categoría=donas" in row_a.motivo


def test_perecedero_limita_cobertura_a_shelf_life() -> None:
    config = _cfg(z=0, cobertura_objetivo_dias=14, presupuesto=1e9, min_residuos=4, default_desviacion=0)
    # Sin límite: 14d×10=140; con shelf_life=2 → 20
    result = recommend_replenishment(
        _forecast(yhat=70), _errors(desviacion=0.0), _inventory(closing=0),
        _products(is_perishable=1, shelf_life_days=2, case_size=1),
        _orders(7), _suppliers(7), config,
    )
    row = result.iloc[0]
    assert row.cantidad == 20
    assert "vida útil=2d" in row.motivo


def test_gap_oct_1_4_usa_yhat_semana_1_sobre_7() -> None:
    rates = daily_rates_from_forecast(
        _forecast(yhat=70), as_of=date(2026, 9, 30), forecast_start=date(2026, 10, 5),
    )
    gap = rates.loc[rates.date.astype(str).isin(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"])]
    assert len(gap) == 4
    assert gap.daily_demand.to_numpy().tolist() == pytest.approx([10.0] * 4)


def test_series_forecast_errors_usa_metodo_por_serie() -> None:
    predictions = pd.DataFrame([
        {"store_id": "S1", "sku": "A", "semana": "2026-01-05", "horizonte": 1,
         "model": "moving_average", "y": 10.0, "yhat": 8.0, "intermittent": False},
        {"store_id": "S1", "sku": "A", "semana": "2026-01-12", "horizonte": 2,
         "model": "moving_average", "y": 12.0, "yhat": 10.0, "intermittent": False},
        {"store_id": "S1", "sku": "A", "semana": "2026-01-05", "horizonte": 1,
         "model": "ridge", "y": 10.0, "yhat": 1.0, "intermittent": False},
    ])
    result = EvalResult(
        predictions=predictions,
        metrics=pd.DataFrame(),
        selection=Selection(False, "moving_average", "test"),
        residuals=predictions.loc[predictions.model.eq("moving_average")].y
        - predictions.loc[predictions.model.eq("moving_average")].yhat,
        histories={("S1", "A"): pd.Series([10.0] * 20).to_numpy()},
        weeks_index={("S1", "A"): pd.date_range("2025-10-06", periods=20, freq="W-MON").to_numpy()},
    )
    errors = series_forecast_errors(result)
    assert list(errors.columns) == ["store_id", "sku", "method", "n_residuos", "sesgo", "desviacion"]
    row = errors.iloc[0]
    assert row.method == "moving_average"
    assert row.n_residuos == 2
    assert row.sesgo == pytest.approx(2.0)
