import os
import csv
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

carpeta = Path("data") / "cleaned"
for archivo in sorted(carpeta.glob("*.csv")):
    with open(archivo, encoding="utf-8", newline="") as f:
        lector = csv.reader(f)
        encabezado = next(lector)
        ejemplo = next(lector, [])
    print("=" * 60)
    print(archivo.name)
    print("COLUMNAS:", encabezado)
    print("EJEMPLO :", ejemplo)

url = os.environ.get("TIMESCALE_SERVICE_URL")
with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';")
        print("=" * 60)
        print("TimescaleDB:", cur.fetchone())