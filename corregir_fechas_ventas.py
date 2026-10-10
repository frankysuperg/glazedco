import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("TIMESCALE_SERVICE_URL")

with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS sales_daily_original AS SELECT * FROM sales_daily")
        cur.execute("ALTER TABLE sales_daily ADD COLUMN IF NOT EXISTS imputado TEXT")
        cur.execute("""
            UPDATE sales_daily s
            SET date = to_date(r.fila->>'date', 'MM/DD/YYYY'),
                imputado = 'date: formato MM/DD/YYYY asumido'
            FROM rejects_sales_daily r
            WHERE r.columna = 'date'
              AND s.date IS NULL
              AND s.store_id = r.fila->>'store_id'
              AND s.sku = r.fila->>'sku'
              AND s.net_sales IS NOT DISTINCT FROM safe_num(r.fila->>'net_sales')
              AND s.units_sold IS NOT DISTINCT FROM safe_int(r.fila->>'units_sold')
        """)
        n = cur.rowcount
        print("Filas actualizadas:", n, "(esperado: 52)")
        if n != 52:
            conn.rollback()
            raise SystemExit("No coincide con 52. Se deshizo todo. Avísame.")
        conn.commit()

        cur.execute("SELECT count(*) FROM sales_daily WHERE date IS NULL")
        print("Filas con date NULL:", cur.fetchone()[0])
        cur.execute("SELECT count(*), sum(net_sales) FROM sales_daily")
        print("Total filas y SUM(net_sales):", cur.fetchone(), "| esperado: (589602, 26298303.64)")
        cur.execute("SELECT min(date), max(date) FROM sales_daily")
        print("Rango de fechas:", cur.fetchone())