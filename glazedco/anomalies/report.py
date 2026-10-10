"""Comparación de WAPE sobre la misma verdad y los mismos pares de validación."""

import numpy as np
import pandas as pd

from glazedco.forecast.evaluate import EvalResult, scores
from .config import AnomalyConfig


def impact_table(baseline: EvalResult, adjusted: EvalResult) -> pd.DataFrame:
    keys = ["store_id", "sku", "semana", "horizonte", "model"]
    paired = baseline.predictions.merge(adjusted.predictions, on=keys, suffixes=("_base", "_ajuste"), validate="one_to_one")
    if not np.allclose(paired.y_base, paired.y_ajuste, equal_nan=True):
        raise ValueError("La verdad de validación cambió entre escenarios")
    paired = paired.loc[np.isfinite(paired.y_base) & np.isfinite(paired.yhat_base) & np.isfinite(paired.yhat_ajuste)]
    rows = []
    for model, sample in paired.groupby("model", sort=True):
        for level, grouping in (("sku_sucursal", None), ("sucursal", ["store_id", "semana", "horizonte"]), ("total", ["semana", "horizonte"])):
            values = sample if grouping is None else sample.groupby(grouping)[["y_base", "yhat_base", "yhat_ajuste"]].sum().reset_index()
            base = scores(values.y_base, values.yhat_base)
            after = scores(values.y_base, values.yhat_ajuste)
            rows.append({"modelo": model, "nivel": level, "n": base["n"], "wape_sin": base["wape"],
                         "wape_con": after["wape"], "diferencia_pp": after["wape"] - base["wape"]})
    return pd.DataFrame(rows)


def render_impact(
    baseline: EvalResult, adjusted: EvalResult, alerts: pd.DataFrame,
    panel: pd.DataFrame, config: AnomalyConfig, source: str,
) -> str:
    comparison = impact_table(baseline, adjusted)
    trained = adjusted.training_panel
    changed = int((panel.entrenable & ~trained.entrenable).sum()) if config.forecast_action == "excluir" else int((panel.units_sold.ne(trained.units_sold) & panel.units_sold.notna()).sum())
    chosen = "ridge" if baseline.selection.keep_ridge else baseline.selection.best_baseline
    lines = ["# Impacto de anomalías en el pronóstico", "", f"Fuente: {source}. Ajuste evaluado: **{config.forecast_action}**, sobre copia; configuración habitual del pronóstico: conservar salvo opción explícita.",
             f"Series semanales: {panel[['store_id', 'sku']].drop_duplicates().shape[0]}; semanas: {panel.semana.nunique()}; observaciones históricas ajustadas: **{changed}**.", "",
             "## Alertas diarias", "", f"Tabla: `reports/anomalies.csv`; **{len(alerts)}** alertas (una fila por métrica; pueden coincidir en un día). Entidad producto = SKU dentro de su sucursal; entidad sucursal = suma diaria.",
             "| Métrica | Alertas |", "|---|---:|"]
    lines += [f"| {metric} | {count} |" for metric, count in alerts.metrica.value_counts().items()]
    lines += ["", f"Sin causa identificada: **{alerts.causa_contextual.eq('sin causa identificada').sum()}**. El contexto es una coincidencia registrada, no una atribución causal ni una tendencia.",
              "", "## WAPE con validación temporal", "",
              "Mismos orígenes, horizontes y valores observados originales en ambos escenarios. Solo se modifica el histórico usado para entrenar y construir rezagos; la ventana de referencia siempre es anterior al punto. WAPE = 100 × suma de errores absolutos / suma de valores observados absolutos; denominador cero produce nulo. Se comparan pares con predicción finita en ambos casos.",
              "", "| Modelo | Nivel | n pares/agregados | Sin ajuste % | Con ajuste % | Diferencia pp |", "|---|---|---:|---:|---:|---:|"]
    def fmt(value: float) -> str:
        return f"{value:.4f}" if np.isfinite(value) else "nulo"
    for row in comparison.itertuples(index=False):
        lines.append(f"| {row.modelo} | {row.nivel} | {row.n} | {fmt(row.wape_sin)} | {fmt(row.wape_con)} | {fmt(row.diferencia_pp)} |")
    reference = comparison.loc[comparison.modelo.eq(chosen) & comparison.nivel.eq("total")]
    if not reference.empty:
        row = reference.iloc[0]
        lines += ["", f"Referencia fija del escenario sin ajuste: **{chosen}**; WAPE total **{fmt(row.wape_sin)}% → {fmt(row.wape_con)}%**, diferencia **{fmt(row.diferencia_pp)} pp** (negativa = mejora). No se adopta el ajuste automáticamente ni se mezclan modelos para aparentar mejora."]
    lines += ["", "## Parámetros y límites", "",
              f"- Mediana y MAD normalizada (1.4826); umbral absoluto de puntaje > {config.threshold:g}; ventana diaria {config.window_days} días, mínimo {config.min_history} observaciones; mismo día semanal={config.same_weekday}. Piso de escala {config.scale_floor:g} unidades/USD y {config.percent_scale_floor:g} puntos de margen.",
              f"- El ajuste de demanda usa el mismo detector en unidades semanales: ventana {config.forecast_window_weeks} semanas y mínimo {config.forecast_min_history} observaciones entrenables. Suavizar reemplaza por mediana previa; excluir conserva la fila y desactiva entrenable. Las alertas de margen no alteran demanda.",
              "- Fechas imputadas fuera de la detección temporal; días sin registros no se convierten en cero. Quiebres/cierres conservan sus exclusiones de entrenamiento. Los quiebres permiten identificar censura, no medir demanda perdida.",
              "- Margen reutiliza glazedco.metrics: net_sales ya descuenta descuentos; costos desconocidos no se rellenan. Monto y porcentaje son métricas distintas. Cobertura de costo en el CSV: cambios en composición/cobertura pueden generar alertas de margen parcial que requieren revisión; costo histórico sin vigencias verificadas.",
              "- No hay calibración etiquetada de falsos positivos. Una alerta aislada no demuestra tendencia. No se han sustituido los archivos de pronóstico vigentes.", "",
              "Reproducir: `uv run --no-sync python -m glazedco.anomalies --adjustment " + config.forecast_action + "`.",
              "Pronóstico con tratamiento explícito: `uv run python -m glazedco.forecast --anomaly-action excluir` (también suavizar o conservar); umbrales mediante `--anomaly-config ruta.toml`.", ""]
    return "\n".join(lines)
