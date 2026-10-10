import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("TIMESCALE_SERVICE_URL")

with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        for tabla in ["prices", "sales_daily", "inventory_daily"]:
            print("=" * 60)
            print("rejects_" + tabla)
            cur.execute(
                "SELECT columna, valor_original, count(*) FROM rejects_" + tabla +
                " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15"
            )
            for fila in cur.fetchall():
                print(fila)

        print("=" * 60)
        for tabla in ["sales_daily", "inventory_daily", "store_daily_kpis"]:
            cur.execute("SELECT count(*) FROM " + tabla + " WHERE date IS NULL")
            print("Filas con date NULL en " + tabla + ":", cur.fetchone()[0])