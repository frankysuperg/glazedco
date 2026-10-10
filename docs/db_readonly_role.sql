-- SOLO PARA REVISIÓN Y EJECUCIÓN MANUAL DEL ADMINISTRADOR. No lo ejecutan agentes.
-- Ejecutar con psql conectado a la base correcta, después de revisar esta lista.
-- Tablas observadas en public; revisar que cada una sea necesaria para el asistente.
-- No incluye staging (stg_*), rechazos (rejects_*) ni sales_daily_original.
-- No otorga permisos sobre tablas futuras, secuencias ni otros esquemas.
-- Revisar privilegios heredados de PUBLIC (incluidos TEMP y funciones con
-- SECURITY DEFINER): los GRANT de este archivo no los revocan globalmente.
-- Elegir un nombre de rol nuevo; no reutilizar un usuario con privilegios previos.
SELECT current_database() AS project_database \gset
BEGIN;
CREATE ROLE glazedco_readonly LOGIN
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE :"project_database" TO glazedco_readonly;
GRANT USAGE ON SCHEMA public TO glazedco_readonly;
GRANT SELECT ON TABLE
    public.calendar,
    public.inventory_daily,
    public.prices,
    public.product_barcodes,
    public.products,
    public.promotions,
    public.purchase_orders,
    public.sales_daily,
    public.sites,
    public.store_closures,
    public.store_daily_kpis,
    public.stores,
    public.suppliers,
    public.weather_daily
TO glazedco_readonly;
ALTER ROLE glazedco_readonly SET default_transaction_read_only = on;
ALTER ROLE glazedco_readonly SET statement_timeout = '15s';
COMMIT;
-- Establecer contraseña por un canal seguro (por ejemplo, \password en psql).
-- Después configurar DATABASE_URL_READONLY en el entorno; nunca en este archivo.
