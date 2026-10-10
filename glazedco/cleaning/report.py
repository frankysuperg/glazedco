"""Reporte agregado; nunca incluye filas de negocio ni credenciales."""

import pandas as pd

from .config import Config
from .core import Result, Rule


def exposure(raw: dict[str, pd.DataFrame], table: str, rule: Rule) -> float:
    sales = raw["sales_daily"].reset_index(drop=True)
    revenue = pd.to_numeric(sales.net_sales, errors="coerce").abs()
    if table == "sales_daily":
        return float(revenue.loc[rule.source_rows].sum())
    keys = {
        "products": ["sku"], "product_barcodes": ["sku"], "stores": ["store_id"],
        "inventory_daily": ["date", "store_id", "sku"], "purchase_orders": ["store_id", "sku"],
        "prices": ["store_id", "sku"], "store_daily_kpis": ["date", "store_id"],
    }[table]
    affected = raw[table].reset_index(drop=True).loc[rule.source_rows, keys].drop_duplicates()
    base = sales[keys].copy()
    for column in keys:
        if column == "date":
            base[column] = pd.to_datetime(base[column])
            affected[column] = pd.to_datetime(affected[column])
    matched = pd.MultiIndex.from_frame(base).isin(pd.MultiIndex.from_frame(affected))
    return float(revenue.loc[matched].sum())


def percentage(numerator: float, denominator: float) -> str:
    return "nulo" if denominator == 0 else f"{100 * numerator / denominator:.6f}%"


def render_report(raw: dict[str, pd.DataFrame], results: dict[str, Result], config: Config) -> str:
    original = pd.to_numeric(raw["sales_daily"].net_sales, errors="coerce")
    denominator = float(original.abs().sum())
    sales = results["sales_daily"][0]
    clean_revenue = float(sales.net_sales.sum())
    known = float(sales.loc[~sales.costo_desconocido & ~sales.costo_estimado, "net_sales"].sum())
    available = float(sales.loc[~sales.costo_desconocido, "net_sales"].sum())
    rejected_revenue = float(pd.to_numeric(results["sales_daily"][1].net_sales, errors="coerce").sum())
    lines = [
        "# Reporte de limpieza", "",
        "Lectura SELECT de las 13 tablas principales; ninguna escritura en PostgreSQL. "
        "Cada fila está en limpios o cuarentena, con `_source_row` relativo a la lectura y `motivo` en cuarentena.", "",
        f"Configuración: cost_fallback={config.cost_fallback}; tolerancia monetaria={config.money_tolerance} USD; "
        f"stockout=[{config.stockout_min}, {config.stockout_max}].", "",
        "## Partición de filas", "", "| Tabla | Leídas | Limpias | Cuarentena |", "|---|---:|---:|---:|",
    ]
    for table, (_, _, summary) in results.items():
        lines.append(f"| {table} | {summary.rows_read} | {summary.rows_clean} | {summary.rows_quarantine} |")
    lines.extend([
        "", "## Ingresos y cobertura", "",
        f"Ingresos netos leídos: USD {float(original.sum()):,.2f}; retenidos: USD {clean_revenue:,.2f}; "
        f"cuarentena: USD {rejected_revenue:,.2f}. No se calcula margen.", "",
        f"Cobertura de costo conocido (sin estimados): **{percentage(known, clean_revenue)}** de ingresos retenidos. "
        f"Cobertura incluyendo estimados: **{percentage(available, clean_revenue)}**. "
        "Si el denominador es cero, el porcentaje es nulo.", "",
        "## Efecto por regla", "",
        f"Denominador de exposición: suma de |net_sales| de ventas leídas = USD {denominator:,.2f}. "
        "Se usan valores absolutos para no ocultar negativos. Para ventas es exposición directa; para "
        "productos/códigos se enlaza por SKU, tiendas por store_id, inventario por día/tienda/SKU, "
        "KPI por día/tienda y órdenes/precios por tienda/SKU durante todo el período. "
        "Es exposición potencial, no pérdida atribuible. Cada venta se cuenta una vez por regla; "
        "las reglas pueden solaparse y sus porcentajes no se suman. Marcas de ventas se miden solo en retenidas.", "",
        "| Tabla / regla | Acción | Filas afectadas | % ingresos expuestos |", "|---|---|---:|---:|",
    ])
    for table, (_, _, summary) in results.items():
        for rule in summary.rules:
            impact = exposure(raw, table, rule)
            lines.append(f"| {table} / {rule.name} | {rule.action} | {rule.affected_rows} | {percentage(impact, denominator)} |")
    names = results["stores"][2].notes.get("tiendas_sin_ventas", [])
    lines.extend(["", "## Tiendas sin ventas", ""])
    lines.extend(f"- {store['store_name']} ({store['store_id']})." for store in names)
    if not names:
        lines.append("Ninguna.")
    lines.extend([
        "", "## Contrato y supuestos", "",
        "- Las funciones clean_<tabla> reciben DataFrames y Config y devuelven (limpios, cuarentena, Summary). "
        "Usar clean_all para respetar el orden: productos únicos antes de unir; uniones many_to_one comprobadas.",
        "- Primera fila significa orden del DataFrame; SQL usa tableoid/ctid dentro de esta lectura. "
        "No representa antigüedad de negocio ni es estable tras reescrituras físicas de la base. "
        "Productos prioriza costo no nulo, completitud original y orden de entrada.",
        "- Tienda sin ventas significa sin registros crudos, aunque sus ventas pudieran terminar en cuarentena. "
        f"Cierres con final inclusivo={config.closure_end_inclusive}; precios con final inclusivo={config.price_end_inclusive}. "
        "Cierres incompletos no permiten determinar una ventana; quedan sin imputar.",
        "- Llaves nulas e importes/fechas de venta incompletos se apartan por defecto para evitar uniones "
        "ambiguas o comprobaciones imposibles. Inventario de balance desconocido u horas nulas se marca inválido y se conserva.",
        "- In transit/Cancelled se guardan en cuarentena como fuera de la población de plazos/cumplimiento, "
        "no como errores de calidad. Otras órdenes requieren cantidades/costo estrictamente positivos y recepción válida. "
        "No se corrigen referencias huérfanas en tablas distintas de ventas ni sobreentregas.",
        "- po_median utiliza órdenes limpias recibidas antes de cada venta; "
        f"permite mismo día={config.fallback_same_day}. Los costos estimados se distinguen de los conocidos. "
        "products.unit_cost no tiene historia de vigencias: su disponibilidad histórica sigue sin verificarse.",
        "- fecha_imputada conserva ingresos y fuerza apta_pronostico=False. Esta marca solo filtra fechas imputadas; "
        "los futuros modelos también deben tratar cierres, quiebres y disponibilidad temporal. "
        "costo_desconocido fuerza incluible_margen=False; no se sustituye costo por cero.",
        "- No se imputan footfall, vida útil ni precios. Inventarios y precios se conservan con marcas; "
        "los porcentajes de exposición no autorizan corregir datos fuente.",
        "", "## Reproducción", "",
        "`uv run python -m glazedco.cleaning` genera este reporte; `--cache` guarda Parquet de limpios "
        "y cuarentena y manifest.json en data/clean/ (ignorado por Git). "
        "`--cost-fallback po_median` activa el fallback; `--config ruta.toml` cambia opciones. "
        "La caché es una instantánea y debe regenerarse cuando cambien los datos o la configuración.",
        "", "Pruebas sintéticas: `uv run pytest tests/test_cleaning.py -q`.",
        "", "API consultada: [pandas.merge](https://pandas.pydata.org/docs/reference/api/pandas.merge.html), "
        "[merge_asof](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html).", "",
    ])
    return "\n".join(lines)
