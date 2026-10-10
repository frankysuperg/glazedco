"""Paso determinista: categorías explícitas y métricas de módulos existentes."""

import pandas as pd

from glazedco.api.data import Dashboard, records
from glazedco.metrics import aggregate_margin
from glazedco.social.models import HashtagResult
from .config import StrategyConfig


def _sum(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.sum()) if len(numeric) and numeric.notna().all() else None


def build_facts(data: Dashboard, trends: HashtagResult, config: StrategyConfig,
                store: str | None = None) -> tuple[dict, list[str]]:
    """Orden original = ranking. Un SKU mapeado selecciona su categoría completa."""
    scope = data.scope(store).model_dump()
    payload = {"hechos_tendencias": [], "alcance": scope,
               "senal_externa": "Hipótesis sin correlación demostrada con ventas",
               "objetivo_pronostico": "Ventas observadas, no demanda insatisfecha"}
    unmapped: list[str] = []
    # Un mapeo vacío no obliga a cargar datos ni modelos internos.
    matched = [(rank, tag, config.hashtags.get(tag.hashtag.casefold()))
               for rank, tag in enumerate(trends.items, 1)]
    if not any(mapping for _, _, mapping in matched):
        return payload, [tag.hashtag for tag in trends.items]
    tables = data.tables()
    products = tables["products"]
    if products.sku.duplicated().any():
        raise ValueError("Catálogo ambiguo")
    end = pd.Timestamp(data.config.as_of_date)
    start = end - pd.Timedelta(days=7 * config.weeks - 1)
    sales = data.select(tables["sales_daily"], store)
    sales = sales.loc[pd.to_datetime(sales.date).between(start, end)]
    inventory = data.select(tables["inventory_daily"], store)
    inventory = inventory.loc[pd.to_datetime(inventory.date).eq(end)]
    forecast = data.select(data.report("forecast"), store)
    forecast = forecast.loc[pd.to_datetime(forecast.semana).gt(end)]
    recommendations = data.recommendations(store)
    category_facts: dict[str, dict] = {}
    for rank, tag, mapping in matched:
        if mapping is None:
            unmapped.append(tag.hashtag)
            continue
        categories = set(mapping.categories)
        selected = products.loc[products.sku.isin(mapping.skus)]
        if set(mapping.skus) - set(selected.sku):
            raise ValueError("SKU configurado ausente del catálogo")
        categories.update(selected.category.dropna())
        if categories - set(products.category.dropna()):
            raise ValueError("Categoría configurada ausente del catálogo")
        if not categories:
            unmapped.append(tag.hashtag)
        for category in sorted(categories):
            if category not in category_facts:
                skus = products.loc[products.category.eq(category), "sku"]
                category_sales = sales.loc[sales.sku.isin(skus)]
                stock = inventory.loc[inventory.sku.isin(skus)]
                recs = recommendations.loc[recommendations.sku.isin(skus)]
                future = forecast.loc[forecast.sku.isin(skus)]
                category_facts[category] = {
                    "categoria": category, "moneda": "USD",
                    "periodo_ventas": {"desde": start.date().isoformat(), "hasta": end.date().isoformat()},
                    "ventas_unidades": _sum(category_sales.units_sold),
                    "margen": records(aggregate_margin(category_sales))[0],
                    "margen_semanal": records(aggregate_margin(category_sales, by="semana")),
                    "fecha_corte_interno": end.date().isoformat(),
                    "inventario": {"existencias_observadas": _sum(stock.closing_qty),
                                   "en_pedido_observado": _sum(stock.on_order_qty),
                                   "posicion_observada": _sum(stock.closing_qty + stock.on_order_qty),
                                   "registros_observados": len(stock),
                                   "cobertura": "Solo registros al corte; ausencias no equivalen a cero"},
                    "riesgo_stock": records(recs[["store_id", "sku", "riesgo_quiebre", "riesgo_exceso"]]),
                    "reabastecimiento": records(recs),
                    "pronostico": [{"semana": pd.Timestamp(week).date().isoformat(),
                                     "unidades": _sum(group.yhat), "series_observadas": len(group)}
                                    for week, group in future.groupby("semana", sort=True)],
                }
            payload["hechos_tendencias"].append({
                "hashtag": tag.hashtag, "posicion_ranking": rank,
                "fecha_consulta": tag.fecha_consulta.isoformat(), "fuente": tag.fuente,
                **category_facts[category],
            })
    return payload, unmapped
