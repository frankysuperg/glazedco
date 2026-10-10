import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("TIMESCALE_SERVICE_URL")

TABLAS = ["sales_daily", "inventory_daily", "store_daily_kpis"]

with psycopg.connect(url, connect_timeout=15, autocommit=True) as conn:
    with conn.cursor() as cur:
        for t in TABLAS:
            try:
                cur.execute(
                    "SELECT create_hypertable(%s, by_range('date', INTERVAL '1 month'), "
                    "if_not_exists => TRUE, migrate_data => TRUE)",
                    (t,),
                )
                print("Hypertable lista:", t)
            except Exception as e:
                print("ERROR en", t, ":", e)

        print()
        print("HYPERTABLES (tabla, chunks):")
        cur.execute("SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables ORDER BY 1")
        for fila in cur.fetchall():
            print(fila)

        print()
        print("VERIFICACION DE CONTEOS:")
        esperado = {
            "calendar": 365, "sites": 20, "stores": 40, "store_closures": 7,
            "suppliers": 8, "promotions": 13, "products": 1018, "prices": 3057,
            "product_barcodes": 1457, "purchase_orders": 262748,
            "sales_daily": 589602, "inventory_daily": 493611,
            "store_daily_kpis": 12784, "weather_daily": 7300,
        }
        for t, n_esp in esperado.items():
            cur.execute('SELECT count(*) FROM "' + t + '"')
            n = cur.fetchone()[0]
            print(t.ljust(18), str(n).rjust(8), "OK" if n == n_esp else "<-- REVISAR (esperado " + str(n_esp) + ")")

        print()
        cur.execute("SELECT sum(net_sales) FROM sales_daily")
        print("SUM(net_sales):", cur.fetchone()[0], "| esperado: 26298303.64")
        cur.execute("SELECT count(*) FROM sales_daily WHERE imputado IS NOT NULL")
        print("Filas de sales_daily con fecha asumida:", cur.fetchone()[0], "| esperado: 52")
        cur.execute("SELECT count(*) FROM inventory_daily WHERE closing_qty IS NULL")
        print("inventory_daily con closing_qty NULL:", cur.fetchone()[0], "| esperado: 0")