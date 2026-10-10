"""Reporte de evaluación; no inventa métricas ausentes."""

from datetime import date

import pandas as pd

from .evaluate import EvalResult, scores
from .models import INTERMITTENT_ADI, adi, is_intermittent


def _fmt(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "nulo"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_Sin filas de evaluación._"
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = "\n".join("| " + " | ".join(_fmt(row[c]) for c in columns) + " |" for _, row in frame.iterrows())
    return "\n".join((header, sep, body))


def panel_summary(panel: pd.DataFrame) -> dict[str, object]:
    n_series = panel.groupby(["store_id", "sku"], dropna=False).ngroups
    adi_values = [
        adi(group.units_sold.where(group.entrenable).to_numpy(dtype=float))
        for _, group in panel.groupby(["store_id", "sku"], dropna=False)
    ]
    return {
        "filas": len(panel),
        "series": n_series,
        "semanas": int(panel.semana.nunique()),
        "entrenables": int(panel.entrenable.sum()),
        "censuradas": int(panel.censurada.sum()),
        "censura_desconocida": int(panel.censura_desconocida.sum()),
        "cierre": int(panel.cierre.sum()),
        "fuera_operacion": int(panel.fuera_operacion.sum()),
        "series_intermitentes": int(sum(is_intermittent(group.units_sold.where(group.entrenable).to_numpy(dtype=float)) for _, group in panel.groupby(["store_id", "sku"], dropna=False))),
        "adi_umbral": INTERMITTENT_ADI,
        "adi_mediana": float(pd.Series(adi_values).median()) if adi_values else float("nan"),
    }


def render_eval_report(panel: pd.DataFrame, result: EvalResult, *, source: str) -> str:
    summary = panel_summary(panel)
    if result.metrics.empty:
        show = result.metrics
        by_horizon = result.metrics
    else:
        show = result.metrics.loc[result.metrics.horizonte.astype(str).eq("1-4"), ["model", "nivel", "mae", "rmse", "wape", "n"]]
        by_horizon = result.metrics.loc[result.metrics.nivel.eq("total") & result.metrics.horizonte.astype(str).ne("1-4"), ["model", "horizonte", "mae", "rmse", "wape", "n"]]
    subset_rows = []
    if not result.predictions.empty:
        for model, piece in result.predictions.groupby("model"):
            for flag, name in ((True, "intermitente"), (False, "regular")):
                sub = piece.loc[piece.intermittent.eq(flag)]
                subset_rows.append({"model": model, "subset": name, **scores(sub.y, sub.yhat)})
    subsets = pd.DataFrame(subset_rows)
    q = result.residuals.quantile([0.1, 0.9]) if result.residuals.notna().any() else pd.Series({0.1: float("nan"), 0.9: float("nan")})
    selection = result.selection
    return "\n".join((
        f"# Evaluación del pronóstico semanal — Glazed Co. · {date.today().isoformat()}",
        "",
        f"Fuente: {source}. Semana lunes–domingo (`W-SUN`), misma regla que `glazedco.metrics`.",
        "Horizonte 1–4 semanas. Validación: `TimeSeriesSplit` de origen móvil sobre el calendario global de semanas.",
        "Unidades: `units_sold` observadas en `sales_daily` limpio con `apta_pronostico=True`.",
        "",
        "## Panel",
        "",
        _table(pd.DataFrame([summary])),
        "",
        "## Censura y demanda no observada",
        "",
        "- Semana **censurada** si el SKU tiene `inventory_tracked=1` y en la semana hay `quiebre` o `closing_qty=0`. No entra al entrenamiento ni al error como demanda 0.",
        "- **censura_desconocida**: SKU rastreado sin filas de inventario esa semana (la auditoría halló ventas sin inventario). No se asume stock ni demanda 0.",
        "- SKU no rastreados (p. ej. bebidas preparadas): no hay inventario; los ceros semanales son ausencia de ventas, no quiebre demostrable.",
        "- Cierres y fechas fuera de `opened_date`/`closed_date` no se entrenan como demanda 0.",
        "- Los quiebres identifican censura; **no cuantifican** demanda perdida. El objetivo es venta observada en semanas entrenables, no demanda latente.",
        "",
        "## Modelos",
        "",
        "- Baselines: ingenuo estacional con periodo 4 (el año semanal m=52 no es identificable con ~52 semanas) y media móvil de 4 semanas entrenables.",
        "- Intermitencia: ADI ≥ 1.32 → Croston con corrección SBA (α=0.1), implementado a mano.",
        "- scikit-learn: Ridge con rezagos 1, 2, 4, media móvil pasada y ciclo de semana ISO. Prophet no se usa: un año semanal no justifica la dependencia.",
        f"- Selección: `{selection.best_baseline}`; Ridge retenido={selection.keep_ridge}. {selection.reason}",
        "",
        "## Métricas (horizontes 1–4 juntos)",
        "",
        "WAPE = ∑|e| / ∑|y| × 100; denominador 0 → nulo. Sucursal y total agregan y e ŷ antes del error.",
        "",
        _table(show),
        "",
        "## Total por horizonte",
        "",
        _table(by_horizon),
        "",
        "## Series intermitentes vs regulares (nivel sku-sucursal)",
        "",
        _table(subsets),
        "",
        "## Bandas",
        "",
        f"Cuantiles de residuos de validación del método elegido (p10={_fmt(float(q.get(0.1, float('nan'))))}, p90={_fmt(float(q.get(0.9, float('nan'))))}), sumados al pronóstico puntual y recortados en 0.",
        "",
        "## Reproducción",
        "",
        "`uv run python -m glazedco.forecast` escribe este archivo. Pruebas: `uv run pytest tests/test_forecast.py -q`.",
        "",
        "API: [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html).",
        "",
    ))
