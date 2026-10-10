"""Lista explícita de tablas; el catálogo solo proporciona sus columnas."""

import os

from glazedco.db import load_environment
from glazedco.sql_validator import SQLPolicy

ALLOWED_TABLES = (
    "sites", "stores", "store_closures", "products", "product_barcodes",
    "suppliers", "prices", "sales_daily", "inventory_daily", "purchase_orders",
    "store_daily_kpis", "promotions", "calendar",
)


def load_policy() -> SQLPolicy:
    load_environment()
    try:
        return SQLPolicy(
            max_rows=int(os.getenv("SQL_MAX_ROWS", "1000")),
            statement_timeout_ms=int(os.getenv("SQL_STATEMENT_TIMEOUT_MS", "5000")),
            schema_name=os.getenv("SQL_SCHEMA", "public"),
        )
    except (ValueError, TypeError):
        raise ValueError("Configuración SQL inválida: revisa límite, timeout y esquema.") from None
