import os
import csv
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
URL = os.environ.get("TIMESCALE_SERVICE_URL")
if not URL:
    raise SystemExit("No se encontró TIMESCALE_SERVICE_URL. Revisa el archivo .env")

CARPETA = Path("data") / "cleaned"

T, D, I, N, H = "TEXT", "DATE", "INTEGER", "NUMERIC", "TIME"
FUNC = {D: "safe_date", I: "safe_int", N: "safe_num", H: "safe_time"}

TABLAS = [
    ("calendar", [("date", D), ("day_of_week", T), ("iso_week", I), ("month", I), ("year", I),
                  ("quarter_idx", I), ("is_weekend", I), ("is_holiday", I), ("holiday_name", T),
                  ("is_school_break", I)], ["date"]),
    ("sites", [("site_id", T), ("site_name", T), ("site_type", T), ("region", T),
               ("traffic_tier", N)], ["site_id"]),
    ("stores", [("store_id", T), ("site_id", T), ("store_name", T), ("format", T),
                ("operator_type", T), ("opening_time", H), ("closing_time", H),
                ("opened_date", D), ("closed_date", D), ("sqft", I), ("seats", I)], ["store_id"]),
    ("store_closures", [("store_id", T), ("start_date", D), ("end_date", D),
                        ("reason", T)], ["store_id", "start_date"]),
    ("suppliers", [("supplier_id", T), ("supplier_name", T), ("category_focus", T),
                   ("standard_lead_time_days", I), ("contract_fill_rate", N),
                   ("contract_on_time_rate", N), ("min_order_value", N),
                   ("delivery_days", T)], ["supplier_id"]),
    ("promotions", [("promo_id", T), ("promo_name", T), ("promo_type", T), ("start_date", D),
                    ("end_date", D), ("discount_pct", N), ("department", T), ("category", T),
                    ("region_scope", T), ("expected_uplift_pct", N)], ["promo_id"]),
    ("products", [("sku", T), ("product_name", T), ("department", T), ("category", T),
                  ("subcategory", T), ("brand", T), ("supplier_id", T), ("unit_cost", N),
                  ("list_price_base", N), ("case_size", I), ("shelf_life_days", I),
                  ("is_perishable", I), ("storage_type", T), ("inventory_tracked", I),
                  ("is_seasonal", I), ("launch_date", D), ("discontinued_date", D),
                  ("status", T)], []),
    ("prices", [("price_id", T), ("store_id", T), ("sku", T), ("unit_price", N),
                ("currency", T), ("effective_from", D), ("effective_to", D),
                ("price_list", T), ("last_updated", D)], []),
    ("product_barcodes", [("barcode", T), ("sku", T), ("barcode_type", T)], []),
    ("purchase_orders", [("po_id", T), ("store_id", T), ("supplier_id", T), ("sku", T),
                         ("order_date", D), ("expected_date", D), ("received_date", D),
                         ("qty_ordered", I), ("qty_received", I), ("unit_cost", N),
                         ("status", T)], []),
    ("sales_daily", [("date", D), ("store_id", T), ("sku", T), ("units_sold", I),
                     ("unit_price", N), ("gross_sales", N), ("discount_amount", N),
                     ("net_sales", N), ("promo_id", T)], []),
    ("inventory_daily", [("date", D), ("store_id", T), ("sku", T), ("opening_qty", I),
                         ("received_qty", I), ("units_sold", I), ("expired_qty", I),
                         ("damaged_qty", I), ("closing_qty", I), ("on_order_qty", I),
                         ("stockout_hours", N)], ["date", "store_id", "sku"]),
    ("store_daily_kpis", [("store_id", T), ("date", D), ("footfall", I), ("transactions", I),
                          ("units_sold", I), ("net_sales", N), ("staff_hours", N),
                          ("open_hours", N)], ["store_id", "date"]),
    ("weather_daily", [("site_id", T), ("date", D), ("temp_max_c", N), ("temp_min_c", N),
                       ("precip_mm", N)], ["site_id", "date"]),
]

FUNCIONES = [
    r"""CREATE OR REPLACE FUNCTION safe_date(t text) RETURNS date LANGUAGE plpgsql AS $$
BEGIN
  IF t IS NULL OR t !~ '^\d{4}-\d{2}-\d{2}$' THEN RETURN NULL; END IF;
  RETURN t::date;
EXCEPTION WHEN others THEN RETURN NULL;
END $$""",
    r"""CREATE OR REPLACE FUNCTION safe_int(t text) RETURNS integer LANGUAGE plpgsql AS $$
BEGIN
  IF t IS NULL OR t !~ '^-?\d+(\.0+)?$' THEN RETURN NULL; END IF;
  RETURN t::numeric::integer;
EXCEPTION WHEN others THEN RETURN NULL;
END $$""",
    r"""CREATE OR REPLACE FUNCTION safe_num(t text) RETURNS numeric LANGUAGE plpgsql AS $$
BEGIN
  IF t IS NULL OR t !~ '^-?\d+(\.\d+)?$' THEN RETURN NULL; END IF;
  RETURN t::numeric;
EXCEPTION WHEN others THEN RETURN NULL;
END $$""",
    r"""CREATE OR REPLACE FUNCTION safe_time(t text) RETURNS time LANGUAGE plpgsql AS $$
BEGIN
  IF t IS NULL OR t !~ '^\d{1,2}:\d{2}(:\d{2})?$' THEN RETURN NULL; END IF;
  RETURN t::time;
EXCEPTION WHEN others THEN RETURN NULL;
END $$""",
]


def expr(col, tipo):
    q = '"' + col + '"'
    return q if tipo == T else FUNC[tipo] + "(" + q + ")"


def contar_filas(archivo):
    with open(archivo, encoding="utf-8", newline="") as f:
        lector = csv.reader(f)
        encabezado = next(lector)
        filas = sum(1 for _ in lector)
    return encabezado, filas


def cargar_tabla(cur, nombre, cols, pk):
    archivo = CARPETA / (nombre + ".csv")
    encabezado, filas_csv = contar_filas(archivo)
    if encabezado != [c for c, _ in cols]:
        raise SystemExit("Las columnas de " + nombre + ".csv no coinciden con lo esperado.")

    stg = "stg_" + nombre
    rej = "rejects_" + nombre
    for t in (stg, nombre, rej):
        cur.execute('DROP TABLE IF EXISTS "' + t + '" CASCADE')

    cur.execute('CREATE TABLE "' + stg + '" (' + ", ".join('"' + c + '" TEXT' for c, _ in cols) + ")")
    defs = ['"' + c + '" ' + t for c, t in cols]
    if pk:
        defs.append("PRIMARY KEY (" + ", ".join('"' + c + '"' for c in pk) + ")")
    cur.execute('CREATE TABLE "' + nombre + '" (' + ", ".join(defs) + ")")
    cur.execute('CREATE TABLE "' + rej + '" (columna TEXT, valor_original TEXT, fila JSONB)')

    # COPY con progreso
    total = archivo.stat().st_size
    leido = 0
    ultimo = -1
    with open(archivo, "rb") as f:
        with cur.copy('COPY "' + stg + '" FROM STDIN WITH (FORMAT csv, HEADER true)') as copy:
            while True:
                datos = f.read(1024 * 1024)
                if not datos:
                    break
                copy.write(datos)
                leido += len(datos)
                pct = int(leido * 100 / total) // 10 * 10
                if pct != ultimo:
                    print("   COPY " + nombre + ": " + str(pct) + "%")
                    ultimo = pct

    cur.execute('SELECT count(*) FROM "' + stg + '"')
    n_stg = cur.fetchone()[0]

    # Insertar en la tabla final con tipos correctos
    nombres = ", ".join('"' + c + '"' for c, _ in cols)
    seleccion = ", ".join(expr(c, t) for c, t in cols)
    cond_pk = " AND ".join(expr(c, t) + " IS NOT NULL" for c, t in cols if c in pk) or "TRUE"
    cur.execute('INSERT INTO "' + nombre + '" (' + nombres + ") SELECT " + seleccion +
                ' FROM "' + stg + '" WHERE ' + cond_pk)

    # Registrar lo no convertible
    for c, t in cols:
        if t == T:
            continue
        cur.execute('INSERT INTO "' + rej + '" (columna, valor_original, fila) SELECT %s, "' + c +
                    '", to_jsonb(s) FROM "' + stg + '" s WHERE "' + c + '" IS NOT NULL AND ' +
                    expr(c, t) + " IS NULL", (c,))

    cur.execute('SELECT count(*) FROM "' + nombre + '"')
    n_fin = cur.fetchone()[0]
    cur.execute('SELECT count(*) FROM "' + rej + '"')
    n_rej = cur.fetchone()[0]
    return filas_csv, n_stg, n_fin, n_rej


def main():
    resumen = []
    inicio = time.time()
    with psycopg.connect(URL, connect_timeout=15, autocommit=True) as conn:
        with conn.cursor() as cur:
            for f in FUNCIONES:
                cur.execute(f)
            for nombre, cols, pk in TABLAS:
                print("== " + nombre)
                t0 = time.time()
                resultado = cargar_tabla(cur, nombre, cols, pk)
                resumen.append((nombre,) + resultado)
                print("   listo en " + str(round(time.time() - t0, 1)) + " s")

            print()
            print("RESUMEN (csv = filas del CSV, stg = en staging, final = en tabla final, rejects = valores no convertibles)")
            for nombre, csv_n, stg_n, fin_n, rej_n in resumen:
                aviso = ""
                if csv_n != stg_n:
                    aviso += "  <-- REVISAR: csv != stg"
                if stg_n != fin_n:
                    aviso += "  <-- REVISAR: stg != final (llave vacia)"
                print(nombre.ljust(18) + " csv=" + str(csv_n).rjust(8) + " stg=" + str(stg_n).rjust(8) +
                      " final=" + str(fin_n).rjust(8) + " rejects=" + str(rej_n).rjust(6) + aviso)

            cur.execute("SELECT sum(net_sales) FROM sales_daily")
            print()
            print("SUM(net_sales) en sales_daily:", cur.fetchone()[0], "| esperado: 26298303.64")
    print("Tiempo total:", round(time.time() - inicio, 1), "s")


main()