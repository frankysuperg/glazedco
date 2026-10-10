# Auditoría de datos — Glazed Co. · 2026-10-10

**Alcance:** 13 tablas de `public`, PostgreSQL/Tiger Data; sin CSV locales. Python/uv; `glazedco/` (Psycopg/dotenv), `tests/` (pytest), `docs/`; FastAPI aún no implementado. Se reutilizó `glazedco.db.Database` mediante SELECT. Otras 30 tablas (`stg_*`, `rejects_*`, `sales_daily_original`, `weather_daily`) solo se catalogaron.

**Mapa:** filas exactas; T=text, I=integer, N=numeric, D=date, H=time; Y=2025-10-01…2026-09-30. Columnas principales abajo; diccionario completo: `--section schema`.

| Tabla | Filas | Columnas clave y tipos; fechas observadas |
|---|---:|---|
| sites | 20 | site_id,site_name,site_type,region:T; traffic_tier:N; sin fechas |
| stores | 40 | store_id,site_id:T; opening_time,closing_time:H; sqft,seats:I; opened_date:D 2019-04-10…2026-07-18; closed_date:D 2026-07-13…08-21 |
| store_closures | 7 | store_id,reason:T; start_date,end_date:D: cobertura 2025-11-16…2026-08-21 |
| products | 1 018 | sku,supplier_id:T; unit_cost,list_price_base:N; case_size,shelf_life_days,inventory_tracked,is_perishable:I; launch_date:D 2021-08-25…2025-08-02; discontinued_date:D 2023-04-17…2025-09-21 |
| product_barcodes | 1 457 | barcode,sku,barcode_type:T; sin fechas |
| suppliers | 8 | supplier_id,delivery_days:T; standard_lead_time_days:I; contract_fill_rate,contract_on_time_rate,min_order_value:N; sin fechas |
| prices | 3 057 | price_id,store_id,sku,currency:T; unit_price:N; effective_from,last_updated:D 2023-07-27…2026-10-14; effective_to:D 2024-11-28…2026-02-28 |
| sales_daily | 589 602 | store_id,sku,promo_id,imputado:T; units_sold:I; unit_price,gross_sales,discount_amount,net_sales:N; date:D Y |
| inventory_daily | 493 611 | store_id,sku:T; opening_qty,received_qty,units_sold,expired_qty,damaged_qty,on_order_qty:I; closing_qty,stockout_hours:N; date:D Y |
| purchase_orders | 262 748 | po_id,store_id,supplier_id,sku,status:T; qty_ordered,qty_received:I; unit_cost:N; order_date:D Y; expected_date:D 2025-10-02…2026-10-07; received_date:D 2025-10-02…2026-09-30 |
| store_daily_kpis | 12 784 | store_id:T; footfall,transactions,units_sold:I; net_sales,staff_hours,open_hours:N; date:D Y |
| promotions | 13 | promo_id,department,category,region_scope:T; discount_pct,expected_uplift_pct:N; start_date,end_date:D: cobertura Y |
| calendar | 365 | date:D Y; day_of_week,holiday_name:T; iso_week,month,year,quarter_idx,is_weekend,is_holiday,is_school_break:I |

**Uniones:** hechos/precios/KPI/cierres → `stores.store_id` → `sites.site_id`; ventas/inventario/órdenes/precios/códigos → `products.sku`; productos/órdenes → `suppliers.supplier_id`; ventas → `promotions.promo_id`; fechas → `calendar.date`. Dimensiones → sucursal mediante hechos. Sin FK declaradas; sin modelar regiones/CD.

**Calidad, por impacto (conteos de filas salvo indicación):**
1. **Margen/uniones:** duplicados por llave: products=27, barcodes=15, órdenes=100, ventas `(date,store_id,sku)`=4; una fila excedente/grupo; duplicados completos=0. Unir productos añade 22 385 ventas. Ventas huérfanas: SKU=7 104, sucursal=74, promoción=60; SKU huérfanos en inventario/órdenes/precios=8 213/3 545/96. 51 303 ventas enlazan con SKU con algún costo nulo: margen no confiable.
2. **Valores:** ventas con ingresos 0=0; cantidades/ingresos negativos=150; `net≠gross−discount`=180, `gross≠units×price`=666 (tolerancia USD 0,01). Descuentos en columna: 90 332 positivos; devoluciones no identificadas explícitamente: no inferirlas de negativos. Inventario: cierres negativos=393, balances incumplidos=2 195. Precios negativos=9; órdenes: cantidades/costos negativos=6/12; KPI: horas negativas=10; productos: vida útil negativa=1.
3. **Tiempo:** ventas antes de apertura=120, durante cierres=200; tiendas=40 versus 38 documentadas (2 sin ventas). Precios: intervalos invertidos=61, pares solapados inclusivos=68. Órdenes: recepciones anteriores al pedido=60, `Delivered` sin fecha recibida=78. Ventas con fecha imputada MM/DD/YYYY=52; CSV no cotejados.

**Nulos por columna (resto=0; no todos son errores):** stores: opening_time=5, closed_date=38; products: unit_cost=40, shelf_life_days=444, discontinued_date=575; prices: unit_price=34, effective_from=23, effective_to=2 292; sales_daily: units_sold=100, promo_id=519 367, imputado=589 550; purchase_orders: expected_date=100, received_date=1 257, qty_received=1 179, unit_cost=17; store_daily_kpis: footfall=50; promotions: category=5; calendar: holiday_name=340. Las 1 179 órdenes `In transit` explican sus cantidades/fechas recibidas nulas.

**Demanda/supuestos propuestos:** quiebres=99 437; stock final 0=218 355; horas de quiebre >24=60; ventas rastreables sin inventario=3 870. Permite identificar censura, no cuantificar demanda perdida. Proponer cuarentena/resolución de llaves, costos desconocidos nulos y excluir cierres/quiebres como demanda 0; estimar plazos/cumplimiento con órdenes recibidas válidas, sin duplicados.

**Preguntas:** ¿tiendas/llaves válidas y registro preferido en duplicados? ¿Significado de ventas negativas? ¿Aprobar fechas imputadas, límites inclusivos y alcance de 13 tablas?

**Reproducir:** `uv run --no-sync python scripts/audit_data.py`; secciones `schema`, `profile`, `quality` ejecutadas sin errores (catálogo/agregados; sin cambios).
