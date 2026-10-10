import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("TIMESCALE_SERVICE_URL")

with psycopg.connect(url, connect_timeout=15, autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE inventory_daily ALTER COLUMN closing_qty TYPE NUMERIC")
        cur.execute(r"""
            UPDATE inventory_daily i
            SET closing_qty = safe_num(s.closing_qty)
            FROM stg_inventory_daily s
            WHERE i.date = safe_date(s."date")
              AND i.store_id = s.store_id
              AND i.sku = s.sku
              AND i.closing_qty IS NULL
              AND s.closing_qty ~ '^-?\d+\.\d+$'
        """)
        print("Filas recuperadas:", cur.rowcount, "(esperado: 40)")
        cur.execute("SELECT count(*) FROM inventory_daily WHERE closing_qty IS NULL")
        print("closing_qty aún NULL:", cur.fetchone()[0])