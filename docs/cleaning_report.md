# Reporte de limpieza

Lectura SELECT de las 13 tablas principales; ninguna escritura en PostgreSQL. Cada fila está en limpios o cuarentena, con `_source_row` relativo a la lectura y `motivo` en cuarentena.

Configuración: cost_fallback=none; tolerancia monetaria=0.01 USD; stockout=[0.0, 24.0].

## Partición de filas

| Tabla | Leídas | Limpias | Cuarentena |
|---|---:|---:|---:|
| sites | 20 | 20 | 0 |
| stores | 40 | 38 | 2 |
| store_closures | 7 | 7 | 0 |
| products | 1018 | 991 | 27 |
| product_barcodes | 1457 | 1442 | 15 |
| suppliers | 8 | 8 | 0 |
| prices | 3057 | 3057 | 0 |
| sales_daily | 589602 | 581021 | 8581 |
| inventory_daily | 493611 | 493611 | 0 |
| purchase_orders | 262748 | 254771 | 7977 |
| store_daily_kpis | 12784 | 12784 | 0 |
| promotions | 13 | 13 | 0 |
| calendar | 365 | 365 | 0 |

## Ingresos y cobertura

Ingresos netos leídos: USD 26,298,303.64; retenidos: USD 25,820,179.05; cuarentena: USD 478,124.59. No se calcula margen.

Cobertura de costo conocido (sin estimados): **90.576747%** de ingresos retenidos. Cobertura incluyendo estimados: **90.576747%**. Si el denominador es cero, el porcentaje es nulo.

## Efecto por regla

Denominador de exposición: suma de |net_sales| de ventas leídas = USD 26,313,560.74. Se usan valores absolutos para no ocultar negativos. Para ventas es exposición directa; para productos/códigos se enlaza por SKU, tiendas por store_id, inventario por día/tienda/SKU, KPI por día/tienda y órdenes/precios por tienda/SKU durante todo el período. Es exposición potencial, no pérdida atribuible. Cada venta se cuenta una vez por regla; las reglas pueden solaparse y sus porcentajes no se suman. Marcas de ventas se miden solo en retenidas.

| Tabla / regla | Acción | Filas afectadas | % ingresos expuestos |
|---|---|---:|---:|
| stores / tienda_sin_ventas | cuarentena | 2 | 0.000000% |
| products / duplicado | cuarentena | 27 | 3.122629% |
| products / llave_nula | cuarentena | 0 | 0.000000% |
| products / vida_util_negativa | marca | 1 | 0.000000% |
| product_barcodes / duplicado | cuarentena | 15 | 0.490822% |
| product_barcodes / llave_nula | cuarentena | 0 | 0.000000% |
| prices / intervalo_invertido | marca | 61 | 1.766591% |
| prices / intervalo_solapado | marca | 134 | 3.048474% |
| sales_daily / duplicado | cuarentena | 4 | 0.000584% |
| sales_daily / llave_nula | cuarentena | 0 | 0.000000% |
| sales_daily / unidades_nulas | cuarentena | 100 | 0.015332% |
| sales_daily / valores_negativos | cuarentena | 150 | 0.028991% |
| sales_daily / importe_nulo | cuarentena | 0 | 0.000000% |
| sales_daily / fecha_invalida | cuarentena | 0 | 0.000000% |
| sales_daily / net_sales_inconsistente | cuarentena | 180 | 0.032690% |
| sales_daily / gross_sales_inconsistente | cuarentena | 666 | 0.106681% |
| sales_daily / sku_inexistente | cuarentena | 7104 | 1.641786% |
| sales_daily / store_id_inexistente | cuarentena | 74 | 0.013139% |
| sales_daily / venta_antes_apertura | cuarentena | 120 | 0.016181% |
| sales_daily / venta_durante_cierre | cuarentena | 200 | 0.021911% |
| sales_daily / promo_inexistente | marca | 59 | 0.007983% |
| sales_daily / fecha_imputada | marca | 50 | 0.010762% |
| sales_daily / costo_desconocido | marca | 51166 | 9.246566% |
| sales_daily / costo_estimado | marca | 0 | 0.000000% |
| inventory_daily / inventario_negativo | marca | 393 | 0.050074% |
| inventory_daily / balance_inventario | marca | 2195 | 0.298355% |
| inventory_daily / horas_quiebre_invalidas | marca | 60 | 0.007988% |
| inventory_daily / balance_desconocido | marca | 0 | 0.000000% |
| inventory_daily / quiebre | marca | 99437 | 14.216449% |
| purchase_orders / duplicado | cuarentena | 100 | 4.650713% |
| purchase_orders / llave_nula | cuarentena | 0 | 0.000000% |
| purchase_orders / orden_pendiente_o_cancelada | cuarentena | 1179 | 51.892053% |
| purchase_orders / estado_no_admitido | cuarentena | 0 | 0.000000% |
| purchase_orders / recepcion_sin_fecha | cuarentena | 78 | 3.600256% |
| purchase_orders / fecha_pedido_invalida | cuarentena | 0 | 0.000000% |
| purchase_orders / recepcion_anterior_pedido | cuarentena | 60 | 3.550622% |
| purchase_orders / cantidad_o_costo_no_positivo | cuarentena | 6576 | 3.384587% |
| store_daily_kpis / staff_hours_negativo | marca | 10 | 0.075040% |
| store_daily_kpis / open_hours_negativo | marca | 0 | 0.000000% |

## Tiendas sin ventas

- DEPARTURES LOBBY – SHOP-IN-SHOP 4 (S039).
- Unknown Terminal Kiosk (S040).

## Contrato y supuestos

- Las funciones clean_<tabla> reciben DataFrames y Config y devuelven (limpios, cuarentena, Summary). Usar clean_all para respetar el orden: productos únicos antes de unir; uniones many_to_one comprobadas.
- Primera fila significa orden del DataFrame; SQL usa tableoid/ctid dentro de esta lectura. No representa antigüedad de negocio ni es estable tras reescrituras físicas de la base. Productos prioriza costo no nulo, completitud original y orden de entrada.
- Tienda sin ventas significa sin registros crudos, aunque sus ventas pudieran terminar en cuarentena. Cierres con final inclusivo=True; precios con final inclusivo=True. Cierres incompletos no permiten determinar una ventana; quedan sin imputar.
- Llaves nulas e importes/fechas de venta incompletos se apartan por defecto para evitar uniones ambiguas o comprobaciones imposibles. Inventario de balance desconocido u horas nulas se marca inválido y se conserva.
- In transit/Cancelled se guardan en cuarentena como fuera de la población de plazos/cumplimiento, no como errores de calidad. Otras órdenes requieren cantidades/costo estrictamente positivos y recepción válida. No se corrigen referencias huérfanas en tablas distintas de ventas ni sobreentregas.
- po_median utiliza órdenes limpias recibidas antes de cada venta; permite mismo día=False. Los costos estimados se distinguen de los conocidos. products.unit_cost no tiene historia de vigencias: su disponibilidad histórica sigue sin verificarse.
- fecha_imputada conserva ingresos y fuerza apta_pronostico=False. Esta marca solo filtra fechas imputadas; los futuros modelos también deben tratar cierres, quiebres y disponibilidad temporal. costo_desconocido fuerza incluible_margen=False; no se sustituye costo por cero.
- No se imputan footfall, vida útil ni precios. Inventarios y precios se conservan con marcas; los porcentajes de exposición no autorizan corregir datos fuente.

## Reproducción

`uv run python -m glazedco.cleaning` genera este reporte; `--cache` guarda Parquet de limpios y cuarentena y manifest.json en data/clean/ (ignorado por Git). `--cost-fallback po_median` activa el fallback; `--config ruta.toml` cambia opciones. La caché es una instantánea y debe regenerarse cuando cambien los datos o la configuración.

Pruebas sintéticas: `uv run pytest tests/test_cleaning.py -q`.

API consultada: [pandas.merge](https://pandas.pydata.org/docs/reference/api/pandas.merge.html), [merge_asof](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html).
