from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from glazedco.cleaning import (
    clean_all, clean_inventory_daily, clean_prices, clean_product_barcodes,
    clean_products, clean_purchase_orders, clean_sales_daily, clean_store_daily_kpis,
    clean_stores, load_config,
)
from glazedco.cleaning.config import Config
from glazedco.cleaning.io import read_tables, save_cache
from glazedco.cleaning.pipeline import TABLES
from glazedco.cleaning.report import percentage, render_report


@pytest.fixture
def config() -> Config:
    return load_config()


@pytest.fixture
def raw() -> dict[str, pd.DataFrame]:
    return {
        "sites": pd.DataFrame([{"site_id": "H1"}]),
        "stores": pd.DataFrame([{"store_id": "S1", "store_name": "Central", "site_id": "H1", "opened_date": "2025-10-01", "closed_date": None}]),
        "store_closures": pd.DataFrame(columns=["store_id", "start_date", "end_date"]),
        "products": pd.DataFrame([{"sku": "A", "unit_cost": 2.0, "shelf_life_days": 2, "supplier_id": "P1", "inventory_tracked": 1}]),
        "product_barcodes": pd.DataFrame([{"barcode": "123", "sku": "A"}]),
        "suppliers": pd.DataFrame([{"supplier_id": "P1"}]),
        "prices": pd.DataFrame([{"store_id": "S1", "sku": "A", "unit_price": 5.0, "effective_from": "2025-10-01", "effective_to": None, "last_updated": "2025-10-01"}]),
        "sales_daily": pd.DataFrame([{"date": "2025-10-10", "store_id": "S1", "sku": "A", "units_sold": 2.0, "unit_price": 5.0, "gross_sales": 10.0, "discount_amount": 1.0, "net_sales": 9.0, "promo_id": "PR1", "imputado": None}]),
        "inventory_daily": pd.DataFrame([{"date": "2025-10-10", "store_id": "S1", "sku": "A", "opening_qty": 5, "received_qty": 3, "units_sold": 2, "expired_qty": 1, "damaged_qty": 1, "closing_qty": 4.0, "stockout_hours": 0.0}]),
        "purchase_orders": pd.DataFrame([{"po_id": "PO1", "store_id": "S1", "sku": "A", "supplier_id": "P1", "order_date": "2025-10-01", "expected_date": "2025-10-03", "received_date": "2025-10-03", "qty_ordered": 10.0, "qty_received": 9.0, "unit_cost": 2.0, "status": "Delivered"}]),
        "store_daily_kpis": pd.DataFrame([{"date": "2025-10-10", "store_id": "S1", "staff_hours": 8.0, "open_hours": 10.0, "footfall": None}]),
        "promotions": pd.DataFrame([{"promo_id": "PR1"}]),
        "calendar": pd.DataFrame([{"date": "2025-10-10"}]),
    }


def assert_partition(original: pd.DataFrame, result: tuple) -> None:
    clean, quarantine, summary = result
    assert len(clean) + len(quarantine) == len(original) == summary.rows_read
    assert len(clean) == summary.rows_clean and len(quarantine) == summary.rows_quarantine
    assert not set(clean._source_row) & set(quarantine._source_row)
    assert sorted([*clean._source_row, *quarantine._source_row]) == list(range(len(original)))
    assert quarantine.motivo.notna().all() and quarantine.motivo.ne("").all()


def test_all_thirteen_partition_and_input_unchanged(raw, config) -> None:
    before = {name: frame.copy(deep=True) for name, frame in raw.items()}
    results = clean_all(raw, config)
    assert set(results) == set(TABLES) and len(results) == 13
    for table in TABLES:
        assert_partition(raw[table], results[table])
        assert_frame_equal(raw[table], before[table])


def test_product_priority_completeness_tie_and_no_sales_multiplication(raw, config) -> None:
    raw["products"] = pd.DataFrame([
        {"sku": "A", "unit_cost": None, "shelf_life_days": 2, "name": "complete"},
        {"sku": "A", "unit_cost": 4.0, "shelf_life_days": None, "name": None},
        {"sku": "A", "unit_cost": 2.0, "shelf_life_days": 2, "name": "first"},
        {"sku": "A", "unit_cost": 3.0, "shelf_life_days": 2, "name": "second"},
    ], index=[9, 9, 7, 2])
    result = clean_all(raw, config)
    assert result["products"][0].iloc[0]["name"] == "first"
    assert result["products"][1].motivo.eq("duplicado").all()
    sales = result["sales_daily"][0]
    assert len(sales) == len(raw["sales_daily"]) == 1
    assert sales.net_sales.sum() == raw["sales_daily"].net_sales.sum()
    assert sales.unit_cost_aplicado.iloc[0] == 2
    assert_partition(raw["products"], result["products"])


def test_sales_refuses_duplicated_master(raw, config) -> None:
    results = clean_all(raw, config)
    with pytest.raises(pd.errors.MergeError):
        clean_sales_daily(raw["sales_daily"], pd.concat([results["products"][0]] * 2), results["stores"][0],
                          results["store_closures"][0], results["promotions"][0], results["purchase_orders"][0], config)


@pytest.mark.parametrize("table", ["sales_daily", "purchase_orders", "product_barcodes"])
def test_deduplication_and_conservation(raw, config, table) -> None:
    raw[table] = pd.concat([raw[table], raw[table]], ignore_index=True)
    result = clean_all(raw, config)[table]
    assert len(result[0]) == len(result[1]) == 1
    assert "duplicado" in result[1].motivo.iloc[0]
    assert_partition(raw[table], result)


def test_po_composite_key_preserves_distinct_lines(raw, config) -> None:
    po = raw["purchase_orders"].iloc[0].to_dict()
    orders = pd.DataFrame([po, {**po, "sku": "B"}, {**po, "store_id": "S2"}, po])
    result = clean_purchase_orders(orders, config)
    assert len(result[0]) == 3 and len(result[1]) == 1
    assert_partition(orders, result)


@pytest.mark.parametrize("column,value,reason", [
    ("units_sold", None, "unidades_nulas"), ("units_sold", -1, "valores_negativos"),
    ("unit_price", -1, "valores_negativos"), ("gross_sales", -1, "valores_negativos"),
    ("net_sales", -1, "valores_negativos"), ("discount_amount", -1, "valores_negativos"),
    ("net_sales", 8, "net_sales_inconsistente"), ("gross_sales", 11, "gross_sales_inconsistente"),
    ("sku", "UNKNOWN", "sku_inexistente"), ("store_id", "UNKNOWN", "store_id_inexistente"),
    ("date", "2025-09-30", "venta_antes_apertura"), ("date", None, "fecha_invalida"),
    ("net_sales", None, "importe_nulo"),
])
def test_sales_quarantine_rules(raw, config, column, value, reason) -> None:
    raw["sales_daily"].loc[0, column] = value
    result = clean_all(raw, config)["sales_daily"]
    assert result[0].empty and reason in result[1].motivo.iloc[0]
    assert_partition(raw["sales_daily"], result)


@pytest.mark.parametrize("net,quarantined", [(9.01, False), (9.011, True)])
def test_money_tolerance_boundary(raw, config, net, quarantined) -> None:
    raw["sales_daily"].loc[0, "net_sales"] = net
    assert bool(len(clean_all(raw, config)["sales_daily"][1])) is quarantined
    assert clean_all(raw, replace(config, money_tolerance=0.1))["sales_daily"][1].empty


@pytest.mark.parametrize("day,closed", [("2025-10-09", False), ("2025-10-10", True), ("2025-10-11", True), ("2025-10-12", False)])
def test_closure_boundaries(raw, config, day, closed) -> None:
    raw["sales_daily"].loc[0, "date"] = day
    raw["store_closures"] = pd.DataFrame([{"store_id": "S1", "start_date": "2025-10-10", "end_date": "2025-10-11"}])
    assert bool(len(clean_all(raw, config)["sales_daily"][1])) is closed


def test_marks_unknown_promo_imputed_date_and_missing_cost(raw, config) -> None:
    raw["sales_daily"].loc[0, "promo_id"] = "bad"
    raw["sales_daily"].loc[0, "imputado"] = "MM/DD/YYYY"
    raw["products"].loc[0, "unit_cost"] = None
    result = clean_all(raw, config)["sales_daily"]
    sale = result[0].iloc[0]
    assert sale.net_sales == 9 and result[1].empty
    assert pd.isna(sale.promo_id) and sale.promo_inexistente
    assert sale.fecha_imputada and not sale.apta_pronostico
    assert sale.costo_desconocido and not sale.incluible_margen and not sale.costo_estimado
    assert pd.isna(sale.unit_cost_aplicado)


def test_fallback_median_is_historical_and_does_not_replace_known_cost(raw, config) -> None:
    po = raw["purchase_orders"].iloc[0].to_dict()
    raw["purchase_orders"] = pd.DataFrame([
        po, {**po, "po_id": "2", "unit_cost": 4, "received_date": "2025-10-04"},
        {**po, "po_id": "3", "unit_cost": 100, "received_date": "2025-10-11"},
        {**po, "po_id": "4", "unit_cost": 200, "received_date": "2025-10-10"},
        {**po, "po_id": "5", "unit_cost": 900, "status": "In transit"},
    ])
    cfg = replace(config, cost_fallback="po_median")
    assert clean_all(raw, cfg)["sales_daily"][0].unit_cost_aplicado.iloc[0] == 2
    raw["products"].loc[0, "unit_cost"] = None
    sale = clean_all(raw, cfg)["sales_daily"][0].iloc[0]
    assert sale.unit_cost_aplicado == 3 and sale.costo_estimado and not sale.costo_desconocido
    raw["sales_daily"].loc[0, "date"] = "2025-10-02"
    assert clean_all(raw, cfg)["sales_daily"][0].costo_desconocido.iloc[0]


def test_stores_without_sales_names(raw, config) -> None:
    raw["stores"] = pd.concat([raw["stores"], pd.DataFrame([{"store_id": "S2", "store_name": "Sin ventas", "opened_date": "2025-01-01", "closed_date": None}])], ignore_index=True)
    result = clean_stores(raw["stores"], raw["sales_daily"], config)
    assert result[0].store_id.tolist() == ["S1"]
    assert result[2].notes["tiendas_sin_ventas"] == [{"store_id": "S2", "store_name": "Sin ventas"}]
    assert_partition(raw["stores"], result)


@pytest.mark.parametrize("column,value,invalid,stockout", [
    ("closing_qty", -1, True, False), ("closing_qty", 3, True, False),
    ("stockout_hours", -1, True, False), ("stockout_hours", 25, True, True),
    ("stockout_hours", 24, False, True), ("stockout_hours", 1, False, True),
    ("stockout_hours", 0, False, False), ("closing_qty", None, True, False),
])
def test_inventory_flags_preserve_rows(raw, config, column, value, invalid, stockout) -> None:
    raw["inventory_daily"].loc[0, column] = value
    result = clean_inventory_daily(raw["inventory_daily"], config)
    row = result[0].iloc[0]
    assert bool(row.inventario_invalido) is invalid and bool(row.quiebre) is stockout
    assert result[1].empty
    assert_partition(raw["inventory_daily"], result)


@pytest.mark.parametrize("column,value,valid", [
    ("status", "Short-shipped", True), ("status", "In transit", False),
    ("status", "Cancelled", False), ("status", "Unknown", False),
    ("received_date", None, False), ("received_date", "2025-09-30", False),
    ("received_date", "2025-10-01", True), ("qty_ordered", 0, False),
    ("qty_ordered", -1, False), ("qty_received", 0, False), ("qty_received", None, False),
    ("unit_cost", 0, False), ("unit_cost", -1, False), ("unit_cost", None, False),
])
def test_po_eligible_population(raw, config, column, value, valid) -> None:
    raw["purchase_orders"].loc[0, column] = value
    result = clean_purchase_orders(raw["purchase_orders"], config)
    assert bool(len(result[0])) is valid
    assert_partition(raw["purchase_orders"], result)


def test_nulls_not_imputed_and_negative_hours_life_flagged(raw, config) -> None:
    raw["store_daily_kpis"].loc[0, ["staff_hours", "open_hours"]] = -1
    raw["products"].loc[0, "shelf_life_days"] = -1
    raw["prices"].loc[0, "unit_price"] = None
    result = clean_all(raw, config)
    kpi = result["store_daily_kpis"][0].iloc[0]
    assert pd.isna(kpi.staff_hours) and pd.isna(kpi.open_hours) and pd.isna(kpi.footfall)
    assert kpi.staff_hours_invalido and kpi.open_hours_invalido
    assert pd.isna(result["products"][0].shelf_life_days.iloc[0])
    assert result["products"][0].vida_util_invalida.iloc[0]
    assert pd.isna(result["prices"][0].unit_price.iloc[0])


def test_prices_overlap_inverted_open_intervals_and_group_separation(raw, config) -> None:
    price = raw["prices"].iloc[0].to_dict()
    frame = pd.DataFrame([
        {**price, "effective_to": "2025-10-10"},
        {**price, "effective_from": "2025-10-10"},
        {**price, "effective_from": "2025-10-20", "effective_to": "2025-10-15"},
        {**price, "store_id": "S2"},
    ])
    result = clean_prices(frame, config)
    assert result[0].intervalo_solapado.tolist() == [True, True, False, False]
    assert result[0].intervalo_invertido.tolist() == [False, False, True, False]
    assert result[1].empty
    assert not clean_prices(frame, replace(config, price_end_inclusive=False))[0].intervalo_solapado.any()
    assert_partition(frame, result)


def test_config_file_overrides_and_invalid_fallback(tmp_path, config) -> None:
    path = tmp_path / "config.toml"
    path.write_text('money_tolerance=0.5\ncost_fallback="po_median"\n', encoding="utf-8")
    assert load_config(path).money_tolerance == .5
    assert load_config(path).cost_fallback == "po_median"
    with pytest.raises(ValueError, match="cost_fallback"):
        replace(config, cost_fallback="zero")


def test_report_revenue_coverage_zero_denominator_and_no_margin(raw, config) -> None:
    report = render_report(raw, clean_all(raw, config), config)
    assert "100.000000%" in report and "No se calcula margen" in report
    raw["products"].loc[0, "unit_cost"] = None
    report = render_report(raw, clean_all(raw, config), config)
    assert "conocido (sin estimados): **0.000000%**" in report
    assert percentage(0, 0) == "nulo"


def test_cache_parquet_roundtrip(raw, config, tmp_path) -> None:
    results = clean_all(raw, config)
    save_cache(results, config, tmp_path)
    assert (tmp_path / "manifest.json").exists()
    assert len(list(tmp_path.glob("*.parquet"))) == 26
    cached = pd.read_parquet(tmp_path / "sales_daily.parquet")
    assert_frame_equal(cached, results["sales_daily"][0].reset_index(drop=True))


def test_reader_select_only_thirteen_tables(config) -> None:
    db = MagicMock()
    connection = db.transaction.return_value.__enter__.return_value
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.description = [type("Column", (), {"name": "id"})()]
    cursor.fetchmany.return_value = []
    tables = read_tables(db, config)
    assert tuple(tables) == TABLES
    assert cursor.execute.call_count == 13
    for call in cursor.execute.call_args_list:
        query = call.args[0].as_string()
        assert query.startswith('SELECT * FROM "public".')
        assert not any(name in query for name in ("stg_", "rejects_", "sales_daily_original", "weather_daily"))


def test_null_keys_are_quarantined_and_raw_quarantine_preserved(raw, config) -> None:
    raw["products"].loc[0, "sku"] = None
    result = clean_products(raw["products"], config)
    assert result[0].empty and result[1].motivo.iloc[0] == "llave_nula"
    assert_frame_equal(result[1][raw["products"].columns].reset_index(drop=True), raw["products"])
    assert_partition(raw["products"], result)


def test_missing_inventory_hours_and_configurable_limit(raw, config) -> None:
    raw["inventory_daily"].loc[0, "stockout_hours"] = None
    assert clean_inventory_daily(raw["inventory_daily"], config)[0].inventario_invalido.iloc[0]
    raw["inventory_daily"].loc[0, "stockout_hours"] = 2
    assert clean_inventory_daily(raw["inventory_daily"], replace(config, stockout_max=1))[0].inventario_invalido.iloc[0]


def test_rule_overlap_does_not_duplicate_quarantine_or_revenue(raw, config) -> None:
    invalid = {**raw["sales_daily"].iloc[0].to_dict(), "sku": "bad", "units_sold": -2, "net_sales": -9}
    raw["sales_daily"] = pd.concat([raw["sales_daily"], pd.DataFrame([invalid])], ignore_index=True)
    result = clean_all(raw, config)["sales_daily"]
    assert len(result[1]) == 1 and "|" in result[1].motivo.iloc[0]
    assert result[0].net_sales.sum() + result[1].net_sales.sum() == raw["sales_daily"].net_sales.sum()
    assert_partition(raw["sales_daily"], result)


def test_zero_revenue_report_and_estimated_coverage(raw, config) -> None:
    raw["products"].loc[0, "unit_cost"] = None
    cfg = replace(config, cost_fallback="po_median")
    report = render_report(raw, clean_all(raw, cfg), cfg)
    assert "conocido (sin estimados): **0.000000%**" in report
    assert "incluyendo estimados: **100.000000%**" in report
    raw["sales_daily"].loc[0, ["units_sold", "gross_sales", "discount_amount", "net_sales"]] = 0
    report = render_report(raw, clean_all(raw, config), config)
    assert "conocido (sin estimados): **nulo**" in report


def test_empty_all_tables_and_partial_key_config(raw, config, tmp_path) -> None:
    empty = {table: frame.iloc[:0].copy() for table, frame in raw.items()}
    results = clean_all(empty, config)
    for table in TABLES:
        assert_partition(empty[table], results[table])
    path = tmp_path / "keys.toml"
    path.write_text('[keys]\nproducts=["sku"]\n', encoding="utf-8")
    assert load_config(path).keys == config.keys
