"""Margen bruto sobre sales_daily limpio, sin I/O ni nuevas uniones.

Ingresos netos elegidos: net_sales, ya neto de discount_amount. No se vuelve
a descontar ni se reconstruye desde precios. La auditoría no identificó
devoluciones explícitas: no se inventan ni se interpretan valores negativos
como devoluciones. Si llegan negativos pese a la limpieza, se invalidan.

COGS = units_sold * unit_cost_aplicado, usando el costo resuelto por cleaning.
Un costo faltante o una exclusión de cleaning deja COGS y margen nulos; su
ingreso válido permanece en ingresos_netos_total, pero queda fuera tanto del
numerador como del denominador del margen. El monto agregado es por tanto
parcial cuando hay exclusiones. El porcentaje se calcula como razón de sumas
de filas válidas, nunca como promedio de porcentajes. Ingreso cero conserva
su monto (incluido un margen negativo) y produce porcentaje nulo.

Los costos estimados admitidos por cleaning participan y se cuentan aparte.
Las fechas imputadas participan en métricas históricas: aquí no hay pronóstico.
Semanas: lunes a domingo, identificadas por la fecha del lunes (sin mezclar años).
"""

from collections.abc import Sequence
from typing import Literal

import pandas as pd

Level = Literal["global", "sucursal", "producto", "semana"]
LEVELS: dict[str, list[str]] = {
    "global": [], "sucursal": ["store_id"], "producto": ["sku"], "semana": ["semana"],
}
REQUIRED = {"net_sales", "units_sold", "unit_cost_aplicado", "costo_desconocido", "incluible_margen", "costo_estimado"}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Float64").replace([float("inf"), -float("inf")], pd.NA)


def _percent(amount: pd.Series, revenue: pd.Series) -> pd.Series:
    return (amount.div(revenue.where(revenue.ne(0))) * 100).astype("Float64")


def margin_rows(clean_sales: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una copia con COGS, monto, porcentaje y motivo de exclusión.

    Entrada: salida limpia sales_daily de glazedco.cleaning, no tabla cruda ni
    cuarentena. Requiere sus campos de costo/eligibilidad para evitar un bypass
    accidental. No muta la entrada ni rellena costos desconocidos con cero.
    margen_invalido incluye negativos, números ausentes/no finitos y marcas
    excluyentes de cleaning. Ingreso cero por sí solo no invalida la fila.
    """
    missing = REQUIRED.difference(clean_sales.columns)
    if missing or "motivo" in clean_sales:
        raise ValueError("Se requiere sales_daily limpio de glazedco.cleaning, con sus campos de costo y elegibilidad.")
    result = clean_sales.copy(deep=True)
    revenue = _numeric(result.net_sales)
    units = _numeric(result.units_sold)
    cost = _numeric(result.unit_cost_aplicado)
    reasons = pd.Series("", index=result.index, dtype=object)
    conditions = {
        "costo_desconocido": cost.isna() | result.costo_desconocido.ne(False).fillna(True),
        "dato_no_finito_o_nulo": revenue.isna() | units.isna(),
        "valor_negativo_no_clasificado": (revenue.lt(0) | units.lt(0) | cost.lt(0)).fillna(False),
        "excluida_por_limpieza": result.incluible_margen.ne(True).fillna(True),
    }
    for reason, mask in conditions.items():
        reasons.loc[mask] = reasons.loc[mask].map(lambda old: f"{old}|{reason}" if old else reason)
    valid = reasons.eq("")
    result["margen_invalido"] = ~valid
    result["motivo_exclusion"] = reasons
    # Negativos inesperados no se convierten en reembolsos dentro del total.
    result["ingresos_netos_total"] = revenue.where(revenue.ge(0) & units.ge(0))
    result["ingresos_base_margen"] = revenue.where(valid)
    result["cogs"] = (units * cost).where(valid)
    result["margen_bruto_monto"] = revenue.sub(result.cogs).where(valid)
    result["margen_bruto_porcentaje"] = _percent(result.margen_bruto_monto, result.ingresos_base_margen)
    result["filas_excluidas"] = (~valid).astype("int64")
    return result


def aggregate_margin(clean_sales: pd.DataFrame, by: str | Sequence[str] | None = None) -> pd.DataFrame:
    """Agrega por store_id, sku, semana o sus combinaciones; None = total.

    Mantiene grupos con llaves nulas para no perder filas. Sin filas válidas,
    COGS y monto son nulos (no cero). filas_excluidas cuenta filas, no motivos;
    margen_parcial y cobertura_ingresos_pct explicitan los límites del monto.
    """
    rows = margin_rows(clean_sales)
    keys = [by] if isinstance(by, str) else list(by or [])
    if len(keys) != len(set(keys)) or set(keys).difference({"store_id", "sku", "semana"}):
        raise ValueError("Agrupar por store_id, sku, semana o combinaciones sin repeticiones")
    if "semana" in keys:
        rows["semana"] = pd.to_datetime(rows.date, errors="coerce").dt.to_period("W-SUN").dt.start_time
    if not keys:
        rows = rows.assign(_total="Todas las sucursales")
    grouped = rows.groupby(keys or ["_total"], dropna=False, sort=True, observed=True)
    amounts = ["ingresos_netos_total", "ingresos_base_margen", "cogs", "margen_bruto_monto"]
    result = grouped[amounts].sum(min_count=1)
    result["filas_totales"] = grouped.size()
    result["filas_excluidas"] = grouped.filas_excluidas.sum()
    rows["_estimada_valida"] = rows.costo_estimado.eq(True).fillna(False) & ~rows.margen_invalido
    result["filas_costo_estimado"] = rows.groupby(keys or ["_total"], dropna=False, sort=True, observed=True)._estimada_valida.sum()
    if not keys and rows.empty:
        result = result.reindex(["Todas las sucursales"])
        result.loc[:, ["filas_totales", "filas_excluidas", "filas_costo_estimado"]] = 0
        result.loc[:, "ingresos_netos_total"] = 0
    result["filas_validas"] = result.filas_totales - result.filas_excluidas
    result["margen_parcial"] = result.filas_excluidas.gt(0)
    result["margen_bruto_porcentaje"] = _percent(result.margen_bruto_monto, result.ingresos_base_margen)
    # Sin filas válidas hay cero ingresos cubiertos; COGS/margen siguen nulos.
    result["cobertura_ingresos_pct"] = _percent(result.ingresos_base_margen.fillna(0), result.ingresos_netos_total)
    result = result.reset_index()
    return result.rename(columns={"_total": "nivel"}) if not keys else result


def margin_by_level(clean_sales: pd.DataFrame, level: Level) -> pd.DataFrame:
    """Atajo para los niveles global, sucursal, producto y semana."""
    if level not in LEVELS:
        raise ValueError(f"Nivel no admitido: {level}")
    return aggregate_margin(clean_sales, LEVELS[level])
