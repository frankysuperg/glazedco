# Impacto de anomalías en el pronóstico

Fuente: PostgreSQL vía glazedco.cleaning (SELECT). Ajuste evaluado: **suavizar**, sobre copia; configuración habitual del pronóstico: conservar salvo opción explícita.
Series semanales: 2169; semanas: 53; observaciones históricas ajustadas: **405**.

## Alertas diarias

Tabla: `reports/anomalies.csv`; **44582** alertas (una fila por métrica; pueden coincidir en un día). Entidad producto = SKU dentro de su sucursal; entidad sucursal = suma diaria.
| Métrica | Alertas |
|---|---:|
| margen_bruto_porcentaje | 15784 |
| ingresos_netos_total | 14097 |
| margen_bruto_monto | 11231 |
| units_sold | 3470 |

Sin causa identificada: **14187**. El contexto es una coincidencia registrada, no una atribución causal ni una tendencia.

## WAPE con validación temporal

Mismos orígenes, horizontes y valores observados originales en ambos escenarios. Solo se modifica el histórico usado para entrenar y construir rezagos; la ventana de referencia siempre es anterior al punto. WAPE = 100 × suma de errores absolutos / suma de valores observados absolutos; denominador cero produce nulo. Se comparan pares con predicción finita en ambos casos.

| Modelo | Nivel | n pares/agregados | Sin ajuste % | Con ajuste % | Diferencia pp |
|---|---|---:|---:|---:|---:|
| moving_average | sku_sucursal | 14860 | 21.7838 | 22.1093 | 0.3255 |
| moving_average | sucursal | 544 | 16.7133 | 16.9981 | 0.2848 |
| moving_average | total | 15 | 14.7591 | 15.0579 | 0.2987 |
| ridge | sku_sucursal | 14778 | 21.2949 | 21.4868 | 0.1919 |
| ridge | sucursal | 544 | 14.4628 | 14.4843 | 0.0215 |
| ridge | total | 15 | 11.9292 | 11.9806 | 0.0514 |
| sba | sku_sucursal | 14860 | 28.4843 | 28.7568 | 0.2725 |
| sba | sucursal | 544 | 21.5728 | 21.8069 | 0.2341 |
| sba | total | 15 | 19.9202 | 20.2154 | 0.2952 |
| seasonal_naive | sku_sucursal | 14860 | 25.3216 | 25.5662 | 0.2446 |
| seasonal_naive | sucursal | 544 | 18.8553 | 19.1273 | 0.2720 |
| seasonal_naive | total | 15 | 16.9853 | 17.3162 | 0.3309 |

Referencia fija del escenario sin ajuste: **ridge**; WAPE total **11.9292% → 11.9806%**, diferencia **0.0514 pp** (negativa = mejora). No se adopta el ajuste automáticamente ni se mezclan modelos para aparentar mejora.

## Parámetros y límites

- Mediana y MAD normalizada (1.4826); umbral absoluto de puntaje > 6; ventana diaria 56 días, mínimo 6 observaciones; mismo día semanal=True. Piso de escala 1 unidades/USD y 0.5 puntos de margen.
- El ajuste de demanda usa el mismo detector en unidades semanales: ventana 12 semanas y mínimo 6 observaciones entrenables. Suavizar reemplaza por mediana previa; excluir conserva la fila y desactiva entrenable. Las alertas de margen no alteran demanda.
- Fechas imputadas fuera de la detección temporal; días sin registros no se convierten en cero. Quiebres/cierres conservan sus exclusiones de entrenamiento. Los quiebres permiten identificar censura, no medir demanda perdida.
- Margen reutiliza glazedco.metrics: net_sales ya descuenta descuentos; costos desconocidos no se rellenan. Monto y porcentaje son métricas distintas. Cobertura de costo en el CSV: cambios en composición/cobertura pueden generar alertas de margen parcial que requieren revisión; costo histórico sin vigencias verificadas.
- No hay calibración etiquetada de falsos positivos. Una alerta aislada no demuestra tendencia. No se han sustituido los archivos de pronóstico vigentes.

Reproducir: `uv run --no-sync python -m glazedco.anomalies --adjustment suavizar`.
Pronóstico con tratamiento explícito: `uv run python -m glazedco.forecast --anomaly-action excluir` (también suavizar o conservar); umbrales mediante `--anomaly-config ruta.toml`.
