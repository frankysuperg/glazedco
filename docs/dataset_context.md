# Contexto del dataset de Glazed Co.

## Alcance y uso

Glazed Co. es una cadena ficticia estadounidense de donas, café, panadería,
sándwiches de desayuno y bebidas, con puntos de venta en aeropuertos, estaciones
de tren, terminales de autobuses y un resort. El dataset es 100 % sintético, en
USD, del 2025-10-01 al 2026-09-30 (365 días), con 20 sitios y 38 tiendas.
Algunas tiendas abrieron, cerraron o tuvieron cierres temporales. Hay picos en
fines de semana, feriados y temporada de viaje. Los productos perecederos duran
de 1 a 3 días; hay quiebres de stock, mermas y entregas tardías o incompletas.
Su uso es exclusivo del hackathon: no se redistribuye ni se presenta como datos reales.

## Tablas y relaciones

Tablas (13): `sites`, `stores`, `store_closures`, `products`, `product_barcodes`,
`suppliers`, `prices`, `sales_daily`, `inventory_daily`, `purchase_orders`,
`store_daily_kpis`, `promotions`, `calendar`.

- `sales_daily`, `inventory_daily`, `purchase_orders` y `prices` se unen a
  `products` por `sku` y a `stores` por `store_id`.
- `stores` se une a `sites` por `site_id`.
- `products.supplier_id` y `purchase_orders.supplier_id` se relacionan con `suppliers`.
- `sales_daily.promo_id` se relaciona con `promotions`; las fechas, con `calendar`.

## Definiciones y decisiones del proyecto

- **Ingresos:** `sales_daily.net_sales`, que ya descuenta `discount_amount`.
  No hay columna de COGS: se calcula como `units_sold × products.unit_cost`.
  No hay columna de devoluciones y no se inventan.
- **Margen bruto:** `net_sales − COGS`; porcentaje: `margen bruto / net_sales × 100`.
  Monto y porcentaje se mantienen separados; con ingresos de 0, el porcentaje es nulo.
- **Jerarquía:** la unidad de análisis es la sucursal (`store_id`) y el total
  consolidado de todas las sucursales. No se modelan centros de distribución ni
  regiones: no usarlos ni inventarlos.
- **Demanda censurada:** `inventory_daily.stockout_hours > 0` indica ventas
  perdidas; esos días no cuentan como demanda baja. Solo los SKUs con
  `inventory_tracked = 1` tienen inventario y órdenes; las bebidas preparadas
  se venden sin control de inventario.
- **Cierres:** los días con la tienda cerrada, según `store_closures`,
  `opened_date` y `closed_date`, no representan demanda 0.
- **Reabastecimiento:** lote mínimo = `products.case_size`; vida útil =
  `shelf_life_days` (lo que la excede se da de baja como `expired_qty`). Las
  condiciones del proveedor están en `suppliers`; las entregas reales, en
  `purchase_orders`, con estados `Delivered`, `Short-shipped`, `In transit` o
  `Cancelled`. `qty_received` puede ser menor que `qty_ordered`. Estimar tiempo
  de entrega y tasa de cumplimiento a partir de esos datos, sin suponerlos.
- **Anomalías:** considerar como causas contextuales `promotions`, `calendar`
  (feriados y vacaciones escolares) y `store_closures`.
- **Pronóstico:** posibles variables explicativas: `calendar` y
  `store_daily_kpis` (`footfall`, `transactions`, `staff_hours`, `open_hours`),
  construidas solo con información disponible al momento de pronosticar.
- **Precios:** `prices` usa `effective_from` y `effective_to` (vacío = vigente).
  Hubo un cambio de precios en parte del catálogo durante el año.
- **Identidad de inventario a verificar:**
  `closing_qty = opening_qty + received_qty − units_sold − expired_qty − damaged_qty`.

## Calidad y pendientes de verificación

El README del dataset avisa de errores de calidad intencionales sin listarlos.
No asumir cuáles son: encontrarlos y documentar su tratamiento corresponde al
Prompt 1. También queda por verificar que las tablas de Tiger Data conserven
los nombres y columnas de los CSV.
