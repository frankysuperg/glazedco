from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from glazedco.metrics import aggregate_margin, margin_by_level, margin_rows
from glazedco.metrics.__main__ import main


def sales(*changes: dict) -> pd.DataFrame:
    base = {"store_id": "S1", "sku": "A", "date": "2026-01-04", "net_sales": 100.0,
            "units_sold": 10.0, "unit_cost_aplicado": 4.0, "costo_desconocido": False,
            "incluible_margen": True, "costo_estimado": False, "fecha_imputada": False}
    return pd.DataFrame([{**base, **change} for change in (changes or ({},))])


def test_normal_pure_and_separate_amount_percent() -> None:
    frame = sales()
    before = frame.copy(deep=True)
    row = margin_rows(frame).iloc[0]
    assert row.cogs == 40 and row.margen_bruto_monto == 60
    assert row.margen_bruto_porcentaje == 60 and not row.margen_invalido
    assert_frame_equal(before, frame)


def test_zero_revenue_keeps_amount_but_null_percent() -> None:
    frame = sales({"net_sales": 0})
    row = margin_rows(frame).iloc[0]
    assert row.margen_bruto_monto == -40 and pd.isna(row.margen_bruto_porcentaje)
    total = aggregate_margin(frame).iloc[0]
    assert total.margen_bruto_monto == -40 and pd.isna(total.margen_bruto_porcentaje)
    assert total.filas_excluidas == 0


def test_missing_cost_preserves_revenue_and_excludes_both_sides_of_ratio() -> None:
    frame = sales({}, {"net_sales": 300, "unit_cost_aplicado": None, "costo_desconocido": True, "incluible_margen": False})
    rows = margin_rows(frame)
    assert rows.margen_invalido.iloc[1] and pd.isna(rows.cogs.iloc[1])
    assert pd.isna(rows.margen_bruto_monto.iloc[1])
    total = aggregate_margin(frame).iloc[0]
    assert total.ingresos_netos_total == 400 and total.ingresos_base_margen == 100
    assert total.margen_bruto_monto == 60 and total.margen_bruto_porcentaje == 60
    assert total.cobertura_ingresos_pct == 25 and total.filas_excluidas == 1 and total.margen_parcial


def test_all_costs_missing_has_no_invented_zero_margin() -> None:
    total = aggregate_margin(sales({"unit_cost_aplicado": None})).iloc[0]
    assert total.filas_excluidas == 1
    assert pd.isna(total.cogs) and pd.isna(total.margen_bruto_monto) and pd.isna(total.margen_bruto_porcentaje)
    assert total.cobertura_ingresos_pct == 0


@pytest.mark.parametrize("change", [{"units_sold": -1}, {"net_sales": -10}, {"units_sold": -1, "net_sales": -10}])
def test_returns_are_not_inferred_from_negatives(change) -> None:
    row = margin_rows(sales(change)).iloc[0]
    assert row.margen_invalido and row.filas_excluidas == 1
    assert "valor_negativo_no_clasificado" in row.motivo_exclusion
    assert pd.isna(row.margen_bruto_monto) and pd.isna(row.margen_bruto_porcentaje)


def test_discount_is_not_subtracted_twice() -> None:
    row = margin_rows(sales({"gross_sales": 120, "discount_amount": 20, "net_sales": 100})).iloc[0]
    assert row.margen_bruto_monto == 60 and row.margen_bruto_porcentaje == 60


def test_sum_stores_equals_global_and_ratio_is_weighted() -> None:
    frame = sales({}, {"store_id": "S2", "net_sales": 300, "units_sold": 20},
                  {"store_id": "S2", "unit_cost_aplicado": None})
    stores = margin_by_level(frame, "sucursal")
    total = margin_by_level(frame, "global").iloc[0]
    for column in ["ingresos_netos_total", "ingresos_base_margen", "cogs", "margen_bruto_monto", "filas_excluidas"]:
        assert stores[column].sum() == total[column]
    assert total.margen_bruto_porcentaje == 70
    assert total.margen_bruto_porcentaje != pytest.approx(stores.margen_bruto_porcentaje.mean())


def test_product_week_year_boundary_and_combined_groups() -> None:
    frame = sales({}, {"sku": "B", "date": "2026-01-05"}, {"date": "2025-01-04"})
    assert len(margin_by_level(frame, "producto")) == 2
    weekly = margin_by_level(frame, "semana")
    assert weekly.semana.tolist() == [pd.Timestamp("2024-12-30"), pd.Timestamp("2025-12-29"), pd.Timestamp("2026-01-05")]
    assert aggregate_margin(frame, ["store_id", "semana"]).margen_bruto_monto.sum() == 180


def test_imputed_dates_and_estimated_cost_follow_cleaning_flags() -> None:
    total = aggregate_margin(sales({"fecha_imputada": True, "costo_estimado": True})).iloc[0]
    assert total.margen_bruto_monto == 60 and total.filas_costo_estimado == 1
    row = margin_rows(sales({"incluible_margen": False})).iloc[0]
    assert row.margen_invalido


@pytest.mark.parametrize("change", [{"unit_cost_aplicado": float("inf")}, {"net_sales": float("nan")}, {"units_sold": None}, {"unit_cost_aplicado": -1}])
def test_nonfinite_missing_negative_numbers_excluded(change) -> None:
    row = margin_rows(sales(change)).iloc[0]
    assert row.margen_invalido and pd.isna(row.margen_bruto_porcentaje)


def test_empty_global_and_null_group_are_not_lost() -> None:
    total = aggregate_margin(sales().iloc[:0]).iloc[0]
    assert total.filas_totales == 0 and pd.isna(total.margen_bruto_monto)
    assert pd.isna(total.margen_bruto_porcentaje)
    groups = margin_by_level(sales({"store_id": None}), "sucursal")
    assert len(groups) == 1 and groups.filas_totales.sum() == 1


def test_raw_and_quarantine_inputs_rejected() -> None:
    with pytest.raises(ValueError, match="limpio"):
        margin_rows(sales().drop(columns="unit_cost_aplicado"))
    with pytest.raises(ValueError, match="limpio"):
        margin_rows(sales({"motivo": "duplicado"}))


def test_command_uses_only_cleaning_output_and_prints_each_level(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["metrics", "--limit", "1"])
    raw = {"sales_daily": pd.DataFrame({"raw_only": [999]})}
    cleaned = sales()
    with patch("glazedco.metrics.__main__.Database", return_value=MagicMock()), \
         patch("glazedco.metrics.__main__.read_tables", return_value=raw), \
         patch("glazedco.metrics.__main__.clean_all", return_value={"sales_daily": (cleaned, None, None)}) as cleaner:
        assert main() == 0
        assert cleaner.call_args.args[0] is raw
    output = capsys.readouterr().out
    for level in ["global", "sucursal", "producto", "semana"]:
        assert f"{level}:" in output
    assert "filas excluidas: 0" in output and "margen_bruto_monto" in output
