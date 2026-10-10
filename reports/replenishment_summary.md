# Resumen de reabastecimiento

Corte: 2026-09-30. Fuente: PostgreSQL vía glazedco.cleaning (SELECT). Presupuesto: sin restricción (vacío).

| Riesgo | Quiebre (filas) | Exceso (filas) |
|---|---:|---:|
| alto | 206 | 294 |
| medio | 82 | 0 |
| bajo | 1038 | 988 |
| desconocido | 42 | 86 |

- Series pronosticadas: **2169**; con recomendación: **1368**; sin fila de recomendación: **801**.
- Desviación de categoría: **1037/1368 (75.80%)** de las filas de salida (incluye SS anulado por demanda cero).
- Filas con algún valor por defecto: **12**.
- Cantidad total pedida: **15138 unidades**; pedidos positivos: **571**; cantidades cero: **797**.
- Sin demanda esperada: **721** filas; apartadas por presupuesto: **0**.

## Series sin recomendación

- sin control de inventario: **705**.
- stock cubre política; riesgos bajos, sin pedido: **63**.
- sin producto maestro: **33**.

## Riesgos desconocidos

- sin inventario al 2026-09-30: **85** filas.
- existencias negativas al 2026-09-30: **1** filas.

## Método y límites

El CSV de errores contiene desviaciones, no residuos individuales. Se usa std(residuos)/media semanal observada por serie, equivalente a std(residuos/media). La mediana de estos coeficientes por categoría se multiplica por la media semanal pronosticada de la serie destino; solo aportan series con min_residuos suficientes y media observada positiva.
La media observada usa semanas históricas aptas según la preparación existente del pronóstico, cerradas al corte. No se estima demanda perdida ni se recalculan modelos. Si falta respaldo utilizable, se declara la desviación por defecto en motivo.
Con demanda cero durante plazo y cobertura, SS y pedido son cero; cobertura infinita, quiebre bajo y exceso según existencias. Un inventario ausente sigue siendo desconocido y su causa se indica.
Los valores por defecto se cuentan una vez por fila (lote, plazo, cumplimiento o desviación); los parámetros de política no se cuentan como imputaciones. Cantidad cero es una recomendación explícita, no una serie ausente.
