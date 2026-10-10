import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

url = os.environ.get("TIMESCALE_SERVICE_URL")
if not url:
    raise SystemExit("No se encontró TIMESCALE_SERVICE_URL. Revisa el archivo .env")

with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        print(cur.fetchone()[0])