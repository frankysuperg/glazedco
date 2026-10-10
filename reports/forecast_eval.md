# Evaluación del pronóstico semanal — Glazed Co. · 2026-10-10

Fuente: PostgreSQL vía glazedco.cleaning (SELECT). Semana lunes–domingo (`W-SUN`), misma regla que `glazedco.metrics`.
Horizonte 1–4 semanas. Validación: `TimeSeriesSplit` de origen móvil sobre el calendario global de semanas.
Unidades: `units_sold` observadas en `sales_daily` limpio con `apta_pronostico=True`.

## Panel

| filas | series | semanas | entrenables | censuradas | censura_desconocida | cierre | fuera_operacion | series_intermitentes | adi_umbral | adi_mediana |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 105238.0000 | 2169.0000 | 53.0000 | 49062.0000 | 55846.0000 | 0.0000 | 1056.0000 | 0.0000 | 744.0000 | 1.3200 | 1.0000 |

## Censura y demanda no observada

- Semana **censurada** si el SKU tiene `inventory_tracked=1` y en la semana hay `quiebre` o `closing_qty=0`. No entra al entrenamiento ni al error como demanda 0.
- **censura_desconocida**: SKU rastreado sin filas de inventario esa semana (la auditoría halló ventas sin inventario). No se asume stock ni demanda 0.
- SKU no rastreados (p. ej. bebidas preparadas): no hay inventario; los ceros semanales son ausencia de ventas, no quiebre demostrable.
- Cierres y fechas fuera de `opened_date`/`closed_date` no se entrenan como demanda 0.
- Los quiebres identifican censura; **no cuantifican** demanda perdida. El objetivo es venta observada en semanas entrenables, no demanda latente.

## Modelos

- Baselines: ingenuo estacional con periodo 4 (el año semanal m=52 no es identificable con ~52 semanas) y media móvil de 4 semanas entrenables.
- Intermitencia: ADI ≥ 1.32 → Croston con corrección SBA (α=0.1), implementado a mano.
- scikit-learn: Ridge con rezagos 1, 2, 4, media móvil pasada y ciclo de semana ISO. Prophet no se usa: un año semanal no justifica la dependencia.
- Selección: `moving_average`; Ridge retenido=True. Ridge reduce el WAPE de validación respecto de ambos baselines.

## Métricas (horizontes 1–4 juntos)

WAPE = ∑|e| / ∑|y| × 100; denominador 0 → nulo. Sucursal y total agregan y e ŷ antes del error.

| model | nivel | mae | rmse | wape | n |
| --- | --- | --- | --- | --- | --- |
| seasonal_naive | sku_sucursal | 19.8973 | 41.1128 | 28.1878 | 15965 |
| seasonal_naive | sucursal | 422.6776 | 620.1801 | 21.7538 | 580 |
| seasonal_naive | total | 14041.1875 | 16270.7427 | 19.9352 | 16 |
| moving_average | sku_sucursal | 17.9602 | 37.8221 | 25.4436 | 15965 |
| moving_average | sucursal | 395.8320 | 615.5377 | 20.3721 | 580 |
| moving_average | total | 13010.4010 | 16678.7383 | 18.4718 | 16 |
| sba | sku_sucursal | 22.2815 | 46.9607 | 31.5653 | 15965 |
| sba | sucursal | 481.5294 | 723.7749 | 24.7827 | 580 |
| sba | total | 16339.0401 | 20061.1016 | 23.1977 | 16 |
| ridge | sku_sucursal | 18.9311 | 33.2687 | 26.7983 | 15845 |
| ridge | sucursal | 394.9567 | 571.7401 | 20.3271 | 580 |
| ridge | total | 12390.8920 | 16500.5519 | 17.5922 | 16 |

## Total por horizonte

| model | horizonte | mae | rmse | wape | n |
| --- | --- | --- | --- | --- | --- |
| seasonal_naive | 1 | 14942.2500 | 15346.8104 | 19.7903 | 4 |
| seasonal_naive | 2 | 11372.5000 | 11616.1423 | 16.0922 | 4 |
| seasonal_naive | 3 | 7248.2500 | 9468.3182 | 10.2346 | 4 |
| seasonal_naive | 4 | 22601.7500 | 24471.2050 | 34.9108 | 4 |
| moving_average | 1 | 10183.2917 | 10347.1413 | 13.4873 | 4 |
| moving_average | 2 | 8933.7500 | 9716.8852 | 12.6414 | 4 |
| moving_average | 3 | 8373.3333 | 9667.2237 | 11.8233 | 4 |
| moving_average | 4 | 24551.2292 | 28596.9377 | 37.9219 | 4 |
| sba | 1 | 15106.1617 | 18370.4115 | 20.0074 | 4 |
| sba | 2 | 12501.7425 | 14499.1509 | 17.6901 | 4 |
| sba | 3 | 13748.5483 | 14969.9729 | 19.4132 | 4 |
| sba | 4 | 23999.7078 | 28948.1210 | 37.0701 | 4 |
| ridge | 1 | 3417.8415 | 4739.0066 | 4.5268 | 4 |
| ridge | 2 | 10115.3807 | 10859.5341 | 14.3134 | 4 |
| ridge | 3 | 11773.8655 | 13533.6437 | 16.6249 | 4 |
| ridge | 4 | 24256.4802 | 27668.1347 | 37.4667 | 4 |

## Series intermitentes vs regulares (nivel sku-sucursal)

| model | subset | mae | rmse | wape | n |
| --- | --- | --- | --- | --- | --- |
| moving_average | intermitente | 9.9235 | 40.6989 | 96.6679 | 802 |
| moving_average | regular | 18.3853 | 37.6638 | 24.9194 | 15163 |
| ridge | intermitente | 7.0842 | 9.2240 | 775.5105 | 682 |
| ridge | regular | 19.4639 | 33.9523 | 26.3814 | 15163 |
| sba | intermitente | 10.7616 | 40.8786 | 104.8316 | 802 |
| sba | regular | 22.8908 | 47.2606 | 31.0261 | 15163 |
| seasonal_naive | intermitente | 9.8529 | 40.6074 | 95.9796 | 802 |
| seasonal_naive | regular | 20.4286 | 41.1394 | 27.6889 | 15163 |

## Bandas

Cuantiles de residuos de validación del método elegido (p10=-35.5536, p90=6.3919), sumados al pronóstico puntual y recortados en 0.

## Reproducción

`uv run python -m glazedco.forecast` escribe este archivo, `reports/forecast_next_weeks.csv` y `reports/forecast_errors.csv` (residuos por serie). Pruebas: `uv run pytest tests/test_forecast.py -q`.

API: [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html).
