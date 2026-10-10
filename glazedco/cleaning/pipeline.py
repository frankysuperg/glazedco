"""Orquestación única de las 13 tablas principales."""

import pandas as pd

from .config import Config
from .core import Result
from .sales import clean_sales_daily
from .tables import (
    clean_calendar, clean_inventory_daily, clean_prices, clean_product_barcodes,
    clean_products, clean_promotions, clean_purchase_orders, clean_sites,
    clean_store_closures, clean_store_daily_kpis, clean_stores, clean_suppliers,
)

TABLES = ("sites", "stores", "store_closures", "products", "product_barcodes",
          "suppliers", "prices", "sales_daily", "inventory_daily", "purchase_orders",
          "store_daily_kpis", "promotions", "calendar")


def clean_all(raw: dict[str, pd.DataFrame], config: Config) -> dict[str, Result]:
    results: dict[str, Result] = {}
    # Productos se deduplican antes de cualquier unión.
    results["products"] = clean_products(raw["products"], config)
    functions = {
        "sites": clean_sites, "suppliers": clean_suppliers,
        "store_closures": clean_store_closures, "promotions": clean_promotions,
        "calendar": clean_calendar, "product_barcodes": clean_product_barcodes,
        "purchase_orders": clean_purchase_orders, "inventory_daily": clean_inventory_daily,
        "store_daily_kpis": clean_store_daily_kpis, "prices": clean_prices,
    }
    for table, cleaner in functions.items():
        results[table] = cleaner(raw[table], config)
    results["stores"] = clean_stores(raw["stores"], raw["sales_daily"], config)
    results["sales_daily"] = clean_sales_daily(
        raw["sales_daily"], results["products"][0], results["stores"][0],
        results["store_closures"][0], results["promotions"][0],
        results["purchase_orders"][0], config,
    )
    return {table: results[table] for table in TABLES}
