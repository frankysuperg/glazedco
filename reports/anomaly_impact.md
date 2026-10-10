# Impacto de anomalías en el pronóstico

Fuente: PostgreSQL vía glazedco.cleaning (SELECT). Ajuste evaluado: **suavizar**, sobre copia; configuración habitual del pronóstico: conservar salvo opción explícita.
Series semanales: 2169; semanas: 53; observaciones históricas ajustadas: **450**.

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
| moving_average | sku_sucursal | 15965 | 25.4436 | 25.7770 | 0.3334 |
| moving_average | sucursal | 580 | 20.3721 | 20.6652 | 0.2931 |
| moving_average | total | 16 | 18.4718 | 18.7784 | 0.3067 |
| ridge | sku_sucursal | 15845 | 26.7983 | 26.9936 | 0.1953 |
| ridge | sucursal | 580 | 20.5018 | 20.4586 | -0.0432 |
| ridge | total | 16 | 18.3483 | 18.1302 | -0.2181 |
| sba | sku_sucursal | 15965 | 31.5653 | 31.8238 | 0.2585 |
| sba | sucursal | 580 | 24.7827 | 25.0004 | 0.2178 |
| sba | total | 16 | 23.1977 | 23.4740 | 0.2764 |
| seasonal_naive | sku_sucursal | 15965 | 28.1878 | 28.4580 | 0.2702 |
| seasonal_naive | sucursal | 580 | 21.7538 | 22.0495 | 0.2958 |
| seasonal_naive | total | 16 | 19.9352 | 20.2883 | 0.3531 |

Referencia fija del escenario sin ajuste: **ridge**; WAPE total **18.3483% → 18.1302%**, diferencia **-0.2181 pp** (negativa = mejora). No se adopta el ajuste automáticamente ni se mezclan modelos para aparentar mejora.

## Parámetros y límites

- Mediana y MAD normalizada (1.4826); umbral absoluto de puntaje > 6; ventana diaria 56 días, mínimo 6 observaciones; mismo día semanal=True. Piso de escala 1 unidades/USD y 0.5 puntos de margen.
- El ajuste de demanda usa el mismo detector en unidades semanales: ventana 12 semanas y mínimo 6 observaciones entrenables. Suavizar reemplaza por mediana previa; excluir conserva la fila y desactiva entrenable. Las alertas de margen no alteran demanda.
- Fechas imputadas fuera de la detección temporal; días sin registros no se convierten en cero. Quiebres/cierres conservan sus exclusiones de entrenamiento. Los quiebres permiten identificar censura, no medir demanda perdida.
- Margen reutiliza glazedco.metrics: net_sales ya descuenta descuentos; costos desconocidos no se rellenan. Monto y porcentaje son métricas distintas. Cobertura de costo en el CSV: cambios en composición/cobertura pueden generar alertas de margen parcial que requieren revisión; costo histórico sin vigencias verificadas.
- No hay calibración etiquetada de falsos positivos. Una alerta aislada no demuestra tendencia. No se han sustituido los archivos de pronóstico vigentes.

Reproducir: `uv run --no-sync python -m glazedco.anomalies --adjustment suavizar`.
Pronóstico con tratamiento explícito: `uv run python -m glazedco.forecast --anomaly-action excluir` (también suavizar o conservar); umbrales mediante `--anomaly-config ruta.toml`.
