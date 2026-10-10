"""Auditoría SELECT reproducible: uv run --no-sync python scripts/audit_data.py."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from glazedco.db import Database, DatabaseError

TABLES = (
    "sites", "stores", "store_closures", "products", "product_barcodes",
    "suppliers", "prices", "sales_daily", "inventory_daily", "purchase_orders",
    "store_daily_kpis", "promotions", "calendar",
)

KEYS = {
    "sites": "site_id", "stores": "store_id", "store_closures": "store_id,start_date",
    "products": "sku", "product_barcodes": "barcode", "suppliers": "supplier_id",
    "prices": "price_id", "sales_daily": "date,store_id,sku",
    "inventory_daily": "date,store_id,sku", "purchase_orders": "po_id",
    "store_daily_kpis": "store_id,date", "promotions": "promo_id", "calendar": "date",
}

QUALITY = {
    "sales_financial": """
        SELECT count(*) FILTER (WHERE net_sales = 0) zero_revenue,
          count(*) FILTER (WHERE discount_amount > 0) discounted_rows,
          count(*) FILTER (WHERE abs(net_sales - (gross_sales-discount_amount)) > .01) net_mismatch,
          count(*) FILTER (WHERE abs(gross_sales-units_sold*unit_price) > .01) gross_mismatch,
          count(*) FILTER (WHERE discount_amount > gross_sales AND gross_sales >= 0) excess_discount,
          count(DISTINCT store_id) stores, count(DISTINCT sku) skus
        FROM public.sales_daily
    """,
    "sales_cost_impact": """
        WITH p AS (SELECT sku, count(*) n, bool_or(unit_cost IS NULL) missing_cost FROM public.products GROUP BY sku)
        SELECT count(*) FILTER (WHERE p.sku IS NULL) orphan_sku,
          count(*) FILTER (WHERE p.n > 1) ambiguous_product_rows,
          count(*) FILTER (WHERE p.missing_cost) missing_cost_rows,
          sum(s.net_sales) FILTER (WHERE p.missing_cost) missing_cost_revenue,
          sum(p.n-1) FILTER (WHERE p.n>1) extra_join_rows
        FROM public.sales_daily s LEFT JOIN p USING (sku)
    """,
    "sales_imputation": "SELECT imputado, count(*) rows FROM public.sales_daily GROUP BY imputado ORDER BY imputado NULLS LAST",
    "inventory": """
        SELECT count(*) FILTER (WHERE closing_qty <> opening_qty+received_qty-units_sold-expired_qty-damaged_qty) balance_mismatch,
          count(*) FILTER (WHERE stockout_hours > 0) stockout_rows,
          count(*) FILTER (WHERE closing_qty=0) zero_stock_rows,
          count(*) FILTER (WHERE stockout_hours>24) excessive_stockout_hours,
          count(*) FILTER (WHERE stockout_hours>0 AND closing_qty>0) stockout_positive_close
        FROM public.inventory_daily
    """,
    "inventory_sales": """
        WITH s AS (SELECT date,store_id,sku,sum(units_sold) units FROM public.sales_daily GROUP BY date,store_id,sku)
        SELECT count(*) FILTER (WHERE s.sku IS NULL) inventory_without_sales,
          count(*) FILTER (WHERE s.sku IS NOT NULL AND s.units <> i.units_sold) unit_mismatch
        FROM public.inventory_daily i LEFT JOIN s USING (date,store_id,sku)
    """,
    "sales_inventory_coverage": """
        SELECT count(*) FILTER (WHERE i.sku IS NULL) tracked_sales_without_inventory,
          count(*) FILTER (WHERE i.stockout_hours>0) censored_sales_rows
        FROM public.sales_daily s LEFT JOIN public.inventory_daily i USING (date,store_id,sku)
        WHERE EXISTS (SELECT 1 FROM public.products p WHERE p.sku=s.sku AND p.inventory_tracked=1)
    """,
    "purchase_status": """
        SELECT status, count(*) rows,
          count(*) FILTER (WHERE received_date IS NULL) missing_received_date,
          count(*) FILTER (WHERE qty_received IS NULL) missing_qty_received,
          count(*) FILTER (WHERE received_date<order_date) negative_lead,
          count(*) FILTER (WHERE received_date>expected_date) late,
          count(*) FILTER (WHERE qty_received<qty_ordered) short,
          count(*) FILTER (WHERE qty_received>qty_ordered) over_received,
          min(received_date-order_date) min_lead, max(received_date-order_date) max_lead,
          round(avg(received_date-order_date) FILTER (WHERE received_date>=order_date),2) mean_nonnegative_lead
        FROM public.purchase_orders GROUP BY status ORDER BY status
    """,
    "supplier_observed": """
        SELECT supplier_id, count(*) eligible_orders,
          round(avg(received_date-order_date),2) mean_lead_days,
          round(sum(qty_received)::numeric/nullif(sum(qty_ordered),0),4) quantity_fill_rate,
          round(avg((received_date<=expected_date)::integer) FILTER (WHERE expected_date IS NOT NULL),4) on_time_rate
        FROM public.purchase_orders
        WHERE status IN ('Delivered','Short-shipped') AND received_date>=order_date
          AND qty_ordered>0 AND qty_received BETWEEN 0 AND qty_ordered
        GROUP BY supplier_id ORDER BY supplier_id
    """,
    "prices": """
        SELECT count(*) FILTER (WHERE effective_to<effective_from) reversed_period,
          count(*) FILTER (WHERE effective_from>DATE '2026-09-30') starts_after_dataset,
          count(*) FILTER (WHERE currency<>'USD') other_currency
        FROM public.prices
    """,
    "price_overlap_pairs": """
        SELECT count(*) pairs FROM public.prices a JOIN public.prices b
          ON a.store_id=b.store_id AND a.sku=b.sku AND a.ctid<b.ctid
         AND a.effective_from<=coalesce(b.effective_to,DATE '9999-12-31')
         AND b.effective_from<=coalesce(a.effective_to,DATE '9999-12-31')
    """,
    "closed_sales": """
        SELECT count(*) FILTER (WHERE s.date<t.opened_date) before_open,
          count(*) FILTER (WHERE s.date>=t.closed_date) on_or_after_close,
          count(*) FILTER (WHERE EXISTS (SELECT 1 FROM public.store_closures c
            WHERE c.store_id=s.store_id AND s.date BETWEEN c.start_date AND c.end_date)) temporary_closed,
          count(*) FILTER (WHERE t.store_id IS NULL) unknown_store
        FROM public.sales_daily s LEFT JOIN public.stores t USING (store_id)
    """,
    "kpi_sales": """
        WITH s AS (SELECT store_id,date,sum(net_sales) revenue,sum(units_sold) units FROM public.sales_daily GROUP BY store_id,date)
        SELECT count(*) FILTER (WHERE s.store_id IS NULL) kpis_without_sales,
          count(*) FILTER (WHERE abs(s.revenue-k.net_sales)>.01) revenue_mismatch,
          count(*) FILTER (WHERE s.units<>k.units_sold) units_mismatch,
          count(*) FILTER (WHERE k.net_sales=0) zero_revenue,
          count(*) FILTER (WHERE k.open_hours=0 AND k.net_sales>0) closed_positive_sales
        FROM public.store_daily_kpis k LEFT JOIN s USING (store_id,date)
    """,
    "product_rules": """
        SELECT count(*) FILTER (WHERE is_perishable=1 AND shelf_life_days IS NULL) perishable_missing_life,
          count(*) FILTER (WHERE is_perishable=1 AND shelf_life_days NOT BETWEEN 1 AND 3) perishable_life_outside_context,
          count(*) FILTER (WHERE case_size<=0) invalid_case_size
        FROM public.products
    """,
    "store_coverage": """
        SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.sales_daily d WHERE d.store_id=s.store_id)) stores_without_sales,
          count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.sites t WHERE t.site_id=s.site_id)) unknown_site
        FROM public.stores s
    """,
}


def quality(database: Database) -> None:
    for table, names in KEYS.items():
        statement = sql.SQL("SELECT count(*) duplicate_keys, coalesce(sum(n-1),0) excess_rows FROM (SELECT count(*) n FROM {} GROUP BY {} HAVING count(*)>1) d").format(
            sql.Identifier("public", table), sql.SQL(",").join(map(sql.Identifier, names.split(","))),
        )
        emit(f"key:{table}", query(database, statement))
    for name, statement in QUALITY.items():
        try:
            with database.transaction() as connection:
                cursor = connection.execute(statement)
                labels = [c.name for c in cursor.description]
                emit(name, [dict(zip(labels, row)) for row in cursor.fetchall()])
        except DatabaseError as exc:
            emit(name, {"error": str(exc)})
    for table, column, parent, parent_column in (
        ("products", "supplier_id", "suppliers", "supplier_id"),
        ("product_barcodes", "sku", "products", "sku"),
        ("sales_daily", "promo_id", "promotions", "promo_id"),
        *((table, field, parent, field) for table in ("prices", "inventory_daily", "purchase_orders")
          for field, parent in (("sku", "products"), ("store_id", "stores"))),
        ("purchase_orders", "supplier_id", "suppliers", "supplier_id"),
        ("store_closures", "store_id", "stores", "store_id"),
        ("store_daily_kpis", "store_id", "stores", "store_id"),
    ):
        statement = sql.SQL("SELECT count(*) FROM {} c WHERE c.{} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {} p WHERE p.{}=c.{})").format(
            sql.Identifier("public", table), sql.Identifier(column), sql.Identifier("public", parent),
            sql.Identifier(parent_column), sql.Identifier(column),
        )
        emit(f"orphans:{table}.{column}", query(database, statement)[0][0])


def emit(name: str, result: Any) -> None:
    print(json.dumps({"section": name, "result": result}, default=str, ensure_ascii=True), flush=True)


def query(database: Database, statement: Any, params: Any = None) -> list[Any]:
    with database.transaction() as connection:
        return connection.execute(statement, params).fetchall()


def schema(database: Database) -> dict[str, list[Any]]:
    columns = query(database, """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
    """, (list(TABLES),))
    result: dict[str, list[Any]] = {table: [] for table in TABLES}
    for table, *column in columns:
        result[table].append(column)
    return result


def profile(database: Database, table: str, columns: list[Any]) -> dict[str, Any]:
    expressions = [sql.SQL("count(*)")]
    labels = ["rows"]
    for name, datatype, _ in columns:
        field = sql.Identifier(name)
        expressions.append(sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(field))
        labels.append(f"null:{name}")
        if datatype in {"date", "timestamp without time zone", "timestamp with time zone"}:
            expressions.extend([sql.SQL("min({})").format(field), sql.SQL("max({})").format(field)])
            labels.extend([f"min:{name}", f"max:{name}"])
        if datatype in {"smallint", "integer", "bigint", "numeric", "real", "double precision"}:
            expressions.append(sql.SQL("count(*) FILTER (WHERE {} < 0)").format(field))
            labels.append(f"negative:{name}")
    statement = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(expressions), sql.Identifier("public", table),
    )
    result = dict(zip(labels, query(database, statement)[0]))
    duplicates = sql.SQL("SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM {} GROUP BY {} HAVING count(*) > 1) d").format(
        sql.Identifier("public", table), sql.SQL(", ").join(sql.Identifier(c[0]) for c in columns),
    )
    result["exact_duplicate_excess"] = query(database, duplicates)[0][0]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=("schema", "profile", "quality", "all"), default="all")
    args = parser.parse_args()
    try:
        with Database(assistant=True) as database:
            columns = schema(database)
            if args.section in {"schema", "all"}:
                emit("schema", columns)
                emit("constraints", query(database, """
                    SELECT c.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
                    FROM pg_catalog.pg_constraint con
                    JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = ANY(%s)
                    ORDER BY c.relname, con.conname
                """, (list(TABLES),)))
                emit("other_tables", query(database, """
                    SELECT tablename FROM pg_catalog.pg_tables
                    WHERE schemaname = 'public' AND NOT (tablename = ANY(%s)) ORDER BY tablename
                """, (list(TABLES),)))
            if args.section in {"profile", "all"}:
                for table, fields in columns.items():
                    try:
                        emit(table, profile(database, table, fields))
                    except DatabaseError as exc:
                        emit(table, {"error": str(exc)})
            if args.section in {"quality", "all"}:
                quality(database)
        return 0
    except DatabaseError as exc:
        emit("error", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
