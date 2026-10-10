import os
import datetime as dt
from collections import Counter

import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("TIMESCALE_SERVICE_URL")

INI = dt.date(2025, 10, 1)
FIN = dt.date(2026, 9, 30)
FORMATOS = [("MM/DD", "%m/%d/%Y"), ("DD/MM", "%d/%m/%Y")]

resumen = Counter()

with psycopg.connect(url, connect_timeout=15) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT fila FROM rejects_sales_daily WHERE columna = 'date'")
        filas = [r[0] for r in cur.fetchall()]
        print("Filas a revisar:", len(filas))

        try:
            for f in filas:
                orig = f["date"]
                try:
                    ns = float(f.get("net_sales"))
                except (TypeError, ValueError):
                    ns = None

                validas = []
                detalle = []
                for nombre, fmt in FORMATOS:
                    try:
                        d = dt.datetime.strptime(orig, fmt).date()
                    except ValueError:
                        detalle.append(nombre + ": no existe")
                        continue
                    if not (INI <= d <= FIN):
                        detalle.append(nombre + " " + str(d) + ": fuera de periodo")
                        continue
                    cur.execute(
                        "SELECT count(*) FROM sales_daily WHERE date=%s AND store_id=%s AND sku=%s",
                        (d, f["store_id"], f["sku"]),
                    )
                    dup = cur.fetchone()[0]
                    if dup > 0:
                        detalle.append(nombre + " " + str(d) + ": ya existe esa llave")
                        continue
                    cur.execute(
                        "SELECT net_sales FROM store_daily_kpis WHERE store_id=%s AND date=%s",
                        (f["store_id"], d),
                    )
                    k = cur.fetchone()
                    cur.execute(
                        "SELECT coalesce(sum(net_sales),0) FROM sales_daily WHERE store_id=%s AND date=%s",
                        (f["store_id"], d),
                    )
                    existente = float(cur.fetchone()[0])
                    cuadra = "sin KPI"
                    if k is not None and k[0] is not None and ns is not None:
                        cuadra = "KPI cuadra" if abs(float(k[0]) - existente - ns) < 0.02 else "KPI no cuadra"
                    validas.append(nombre)
                    detalle.append(nombre + " " + str(d) + ": valida, " + cuadra)

                resultado = "/".join(validas) if validas else "NINGUNA"
                resumen[resultado] += 1
                print(orig, f["store_id"], f["sku"], "->", resultado, "|", "; ".join(detalle))
        except Exception as e:
            print("ERROR:", e)

print()
print("RESUMEN (lecturas validas por fila):", dict(resumen))