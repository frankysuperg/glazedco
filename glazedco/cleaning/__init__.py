"""API de limpieza: cada clean_<tabla> devuelve (DataFrame, cuarentena, Summary)."""

from .config import Config, load_config
from .pipeline import clean_all
from .sales import clean_sales_daily
from .tables import (
    clean_calendar, clean_inventory_daily, clean_prices, clean_product_barcodes,
    clean_products, clean_promotions, clean_purchase_orders, clean_sites,
    clean_store_closures, clean_store_daily_kpis, clean_stores, clean_suppliers,
)

__all__ = ["Config", "load_config", "clean_all", "clean_calendar", "clean_inventory_daily",
           "clean_prices", "clean_product_barcodes", "clean_products", "clean_promotions",
           "clean_purchase_orders", "clean_sales_daily", "clean_sites", "clean_store_closures",
           "clean_store_daily_kpis", "clean_stores", "clean_suppliers"]
