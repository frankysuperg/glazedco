"""Resumen reproducible de recomendaciones y series excluidas."""

import pandas as pd

from .config import ReplenishmentConfig


def render_summary(
    result: pd.DataFrame,
    forecast: pd.DataFrame,
    products: pd.DataFrame,
    config: ReplenishmentConfig,
    source: str,
) -> str:
    """Cuenta filas de salida; cantidad cero sigue siendo una recomendación."""
    keys = ["store_id", "sku"]
    series = forecast[keys].drop_duplicates()
    missing = series.merge(result[keys], on=keys, how="left", indicator=True, validate="one_to_one")
    missing = missing.loc[missing._merge.eq("left_only"), keys].merge(
        products[["sku", "inventory_tracked"]].drop_duplicates("sku"),
        on="sku", how="left", indicator=True, validate="many_to_one",
    )
    missing["causa"] = "stock cubre política; riesgos bajos, sin pedido"
    tracked = pd.to_numeric(missing.inventory_tracked, errors="coerce")
    missing.loc[tracked.ne(1), "causa"] = "sin control de inventario"
    missing.loc[tracked.isna(), "causa"] = "control de inventario desconocido"
    missing.loc[missing._merge.eq("left_only"), "causa"] = "sin producto maestro"
    category = int(result.fuente_desviacion.eq("categoria_relativa").sum())
    percentage = f"{100 * category / len(result):.2f}%" if len(result) else "no aplica"
    budget = "sin restricción (vacío)" if config.presupuesto is None else f"{config.presupuesto:g} USD"
    lines = [
        "# Resumen de reabastecimiento", "",
        f"Corte: {config.as_of_date}. Fuente: {source}. Presupuesto: {budget}.", "",
        "| Riesgo | Quiebre (filas) | Exceso (filas) |", "|---|---:|---:|",
    ]
    for level in ("alto", "medio", "bajo", "desconocido"):
        lines.append(f"| {level} | {result.riesgo_quiebre.eq(level).sum()} | {result.riesgo_exceso.eq(level).sum()} |")
    lines += [
        "",
        f"- Series pronosticadas: **{len(series)}**; con recomendación: **{len(result)}**; sin fila de recomendación: **{len(missing)}**.",
        f"- Desviación de categoría: **{category}/{len(result)} ({percentage})** de las filas de salida (incluye SS anulado por demanda cero).",
        f"- Filas con algún valor por defecto: **{int(result.valores_por_defecto.eq(True).sum())}**.",
        f"- Cantidad total pedida: **{int(result.cantidad.sum())} unidades**; pedidos positivos: **{int(result.cantidad.gt(0).sum())}**; cantidades cero: **{int(result.cantidad.eq(0).sum())}**.",
        f"- Sin demanda esperada: **{int(result.motivo.str.contains('sin demanda esperada', regex=False).sum())}** filas; apartadas por presupuesto: **{int(result.motivo.str.contains('Fuera de presupuesto', regex=False).sum())}**.",
        "", "## Series sin recomendación", "",
    ]
    lines += [f"- {cause}: **{count}**." for cause, count in missing.causa.value_counts().items()] or ["- Ninguna."]
    unknown = result.loc[result.riesgo_quiebre.eq("desconocido") | result.riesgo_exceso.eq("desconocido")]
    lines += ["", "## Riesgos desconocidos", ""]
    reasons = unknown.motivo.map(lambda text: "; ".join(
        part for part in text.split("; ")
        if part.startswith(("sin inventario al", "existencias ", "en tránsito ", "sin lead time válido"))
    ) or text)
    lines += [f"- {reason}: **{count}** filas." for reason, count in reasons.value_counts().items()] or ["- Ninguno."]
    lines += [
        "", "## Método y límites", "",
        "El CSV de errores contiene desviaciones, no residuos individuales. Se usa std(residuos)/media semanal observada por serie, equivalente a std(residuos/media). La mediana de estos coeficientes por categoría se multiplica por la media semanal pronosticada de la serie destino; solo aportan series con min_residuos suficientes y media observada positiva.",
        "La media observada usa semanas históricas aptas según la preparación existente del pronóstico, cerradas al corte. No se estima demanda perdida ni se recalculan modelos. Si falta respaldo utilizable, se declara la desviación por defecto en motivo.",
        "Con demanda cero durante plazo y cobertura, SS y pedido son cero; cobertura infinita, quiebre bajo y exceso según existencias. Un inventario ausente sigue siendo desconocido y su causa se indica.",
        "Los valores por defecto se cuentan una vez por fila (lote, plazo, cumplimiento o desviación); los parámetros de política no se cuentan como imputaciones. Cantidad cero es una recomendación explícita, no una serie ausente.", "",
    ]
    return "\n".join(lines)
