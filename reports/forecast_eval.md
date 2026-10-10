# Evaluación del pronóstico semanal — Glazed Co. · 2026-10-10

Fuente: PostgreSQL vía glazedco.cleaning (SELECT). Semana lunes–domingo (`W-SUN`), misma regla que `glazedco.metrics`.
Horizonte 1–4 semanas. Validación: `TimeSeriesSplit` de origen móvil sobre el calendario global de semanas.
Unidades: `units_sold` observadas en `sales_daily` limpio con `apta_pronostico=True`.

## Panel

| filas | series | semanas | entrenables | censuradas | censura_desconocida | cierre | fuera_operacion | series_intermitentes | adi_umbral | adi_mediana |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 105238.0000 | 2169.0000 | 53.0000 | 47344.0000 | 55846.0000 | 0.0000 | 1056.0000 | 0.0000 | 776.0000 | 1.3200 | 1.0000 |

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
| seasonal_naive | sku_sucursal | 18.6748 | 39.3115 | 25.3216 | 14860 |
| seasonal_naive | sucursal | 379.8548 | 550.9760 | 18.8553 | 544 |
| seasonal_naive | total | 12409.8000 | 13546.5099 | 16.9853 | 15 |
| moving_average | sku_sucursal | 16.0656 | 34.0892 | 21.7838 | 14860 |
| moving_average | sucursal | 336.7021 | 499.3154 | 16.7133 | 544 |
| moving_average | total | 10783.3167 | 12373.0300 | 14.7591 | 15 |
| sba | sku_sucursal | 21.0073 | 45.4003 | 28.4843 | 14860 |
| sba | sucursal | 434.6010 | 650.7439 | 21.5728 | 544 |
| sba | total | 14554.1236 | 17435.8744 | 19.9202 | 15 |
| ridge | sku_sucursal | 15.6935 | 28.8661 | 21.2949 | 14778 |
| ridge | sucursal | 291.2941 | 428.1016 | 14.4593 | 544 |
| ridge | total | 8331.3853 | 11086.9355 | 11.4032 | 15 |

## Total por horizonte

| model | horizonte | mae | rmse | wape | n |
| --- | --- | --- | --- | --- | --- |
| seasonal_naive | 1 | 14942.2500 | 15346.8104 | 19.7903 | 4 |
| seasonal_naive | 2 | 11372.5000 | 11616.1423 | 16.0922 | 4 |
| seasonal_naive | 3 | 7248.2500 | 9468.3182 | 10.2346 | 4 |
| seasonal_naive | 4 | 17298.3333 | 17437.3675 | 22.7658 | 3 |
| moving_average | 1 | 10183.2917 | 10347.1413 | 13.4873 | 4 |
| moving_average | 2 | 8933.7500 | 9716.8852 | 12.6414 | 4 |
| moving_average | 3 | 8373.3333 | 9667.2237 | 11.8233 | 4 |
| moving_average | 4 | 17262.7500 | 19292.7658 | 22.7189 | 3 |
| sba | 1 | 15070.8502 | 18256.5168 | 19.9606 | 4 |
| sba | 2 | 12466.4303 | 14416.2205 | 17.6402 | 4 |
| sba | 3 | 13713.2247 | 14888.7747 | 19.3633 | 4 |
| sba | 4 | 17769.9443 | 22427.1561 | 23.3864 | 3 |
| ridge | 1 | 3021.6760 | 3798.9462 | 4.0021 | 4 |
| ridge | 2 | 7101.3078 | 8140.5908 | 10.0484 | 4 |
| ridge | 3 | 7475.5349 | 9994.8453 | 10.5556 | 4 |
| ridge | 4 | 18192.2348 | 19333.9902 | 23.9422 | 3 |

## Series intermitentes vs regulares (nivel sku-sucursal)

| model | subset | mae | rmse | wape | n |
| --- | --- | --- | --- | --- | --- |
| moving_average | intermitente | 9.9514 | 42.4873 | 96.3901 | 722 |
| moving_average | regular | 16.3779 | 33.6041 | 21.2729 | 14138 |
| ridge | intermitente | 4.9141 | 6.6151 | 522.4318 | 640 |
| ridge | regular | 16.1814 | 29.4786 | 21.0177 | 14138 |
| sba | intermitente | 10.8709 | 42.6780 | 105.2959 | 722 |
| sba | regular | 21.5249 | 45.5350 | 27.9583 | 14138 |
| seasonal_naive | intermitente | 9.8490 | 42.3886 | 95.3984 | 722 |
| seasonal_naive | regular | 19.1255 | 39.1479 | 24.8417 | 14138 |

## Bandas

Cuantiles de residuos de validación del método elegido (p10=-27.7827, p90=10.4747), sumados al pronóstico puntual y recortados en 0.

## Reproducción

`uv run python -m glazedco.forecast` escribe este archivo, `reports/forecast_next_weeks.csv` y `reports/forecast_errors.csv` (residuos por serie). Pruebas: `uv run pytest tests/test_forecast.py -q`.

API: [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html).

Tratamiento de anomalías en copia: conservar; verdad de validación sin ajuste.
