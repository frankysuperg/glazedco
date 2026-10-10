#!/usr/bin/env python3
"""Auditoría y limpieza reproducible de los CSV de Glazed Co.
Uso:  python clean_data.py [carpeta_proyecto]
Lee  data/raw/*.csv (NUNCA los modifica) y escribe data/cleaned/ y data/reports/.
Python 3.12 + pandas + numpy. Se puede re-ejecutar: borra y regenera solo sus salidas."""
import sys, hashlib, shutil, re
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
RAW, CLEAN, REP = BASE / "data" / "raw", BASE / "data" / "cleaned", BASE / "data" / "reports"
DETAIL = REP / "detail"
CAP = 500  # máx. filas por regla en anomalies/manual_review (el detalle completo va a reports/detail/)
TOKENS = {"#N/A", "N/A", "NULL", "NaN", "nan", "None", "#NULL!"}
FILES = ["calendar", "inventory_daily", "prices", "product_barcodes", "products", "promotions",
         "purchase_orders", "sales_daily", "sites", "store_closures", "store_daily_kpis", "stores",
         "suppliers", "weather_daily"]
KEYS = {"calendar": ["date"], "inventory_daily": ["date", "store_id", "sku"],
        "prices": ["store_id", "sku", "effective_from"], "product_barcodes": ["barcode"],
        "products": ["sku"], "promotions": ["promo_id"], "purchase_orders": ["po_id", "store_id", "sku"],
        "sales_daily": ["date", "store_id", "sku"], "sites": ["site_id"],
        "store_closures": ["store_id", "start_date"], "store_daily_kpis": ["store_id", "date"],
        "stores": ["store_id"], "suppliers": ["supplier_id"], "weather_daily": ["site_id", "date"]}
DATES = {"calendar": ["date"], "inventory_daily": ["date"], "prices": ["effective_from", "effective_to", "last_updated"],
         "products": ["launch_date", "discontinued_date"], "promotions": ["start_date", "end_date"],
         "purchase_orders": ["order_date", "expected_date", "received_date"], "sales_daily": ["date"],
         "store_closures": ["start_date", "end_date"], "store_daily_kpis": ["date"],
         "stores": ["opened_date", "closed_date"], "weather_daily": ["date"]}
NUMS = {"inventory_daily": ["opening_qty", "received_qty", "units_sold", "expired_qty", "damaged_qty", "closing_qty", "on_order_qty", "stockout_hours"],
        "prices": ["unit_price"], "products": ["unit_cost", "list_price_base", "case_size", "shelf_life_days", "is_perishable", "inventory_tracked", "is_seasonal"],
        "promotions": ["discount_pct", "expected_uplift_pct"],
        "purchase_orders": ["qty_ordered", "qty_received", "unit_cost"],
        "sales_daily": ["units_sold", "unit_price", "gross_sales", "discount_amount", "net_sales"],
        "sites": ["traffic_tier"], "store_daily_kpis": ["footfall", "transactions", "units_sold", "net_sales", "staff_hours", "open_hours"],
        "stores": ["sqft", "seats"], "suppliers": ["standard_lead_time_days", "contract_fill_rate", "contract_on_time_rate", "min_order_value"],
        "weather_daily": ["temp_max_c", "temp_min_c", "precip_mm"]}
HINTS = {("products", "shelf_life_days"): "Vacío en SKUs no perecederos/bebidas preparadas: probablemente 'no aplica' (no es cero).",
         ("products", "discontinued_date"): "Vacío = SKU no descontinuado (legítimo).",
         ("products", "unit_cost"): "Costo desconocido; no se imputa.",
         ("prices", "effective_to"): "Vacío = precio vigente (según README).",
         ("prices", "unit_price"): "Precio desconocido; no se imputa.",
         ("promotions", "category"): "Vacío = promoción a nivel departamento (probablemente legítimo).",
         ("purchase_orders", "qty_received"): "Vacío en 'In transit' = aún no recibido (no es cero).",
         ("purchase_orders", "received_date"): "Vacío en 'In transit'/cancelada = sin recepción.",
         ("purchase_orders", "expected_date"): "Fecha esperada desconocida; no se imputa.",
         ("sales_daily", "promo_id"): "Vacío = venta sin promoción (legítimo).",
         ("sales_daily", "units_sold"): "Unidades desconocidas; no se imputan (ver manual_review).",
         ("stores", "closed_date"): "Vacío = tienda sin cierre definitivo.",
         ("stores", "opening_time"): "Horario desconocido; no se imputa.",
         ("store_daily_kpis", "footfall"): "Afluencia no registrada; NO es lo mismo que 0.",
         ("weather_daily", "precip_mm"): "Precipitación no registrada; no se imputa (no es 0 mm)."}

LOGP, DUPP, SKUP, MISSP, RELP, ANOMP, REVP = [], [], [], [], [], [], []
RULES = []


def ks(df, keys):
    if len(df) == 0:
        return pd.Series([], dtype=str)
    return df[keys].fillna("").astype(str).agg("|".join, axis=1)


def num(df, c):
    return pd.to_numeric(df[c], errors="coerce")


def pdate(s):
    return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def emit(kind, file, rule, cat, reason, df, mask, keys, vals=(), cap=CAP, action=""):
    sub = df[mask]
    n = len(sub)
    if n == 0:
        return 0
    RULES.append(dict(file=file, rule=rule, category=cat, cases=n))
    detail = ""
    if n > cap and cap == CAP:
        DETAIL.mkdir(parents=True, exist_ok=True)
        detail = f"detail/{file}__{rule}.csv"
        d = sub.drop(columns=[c for c in sub.columns if c.startswith("_")])
        d.insert(0, "source_line", sub["_ln"].values)
        d.to_csv(REP / detail, index=False)
    s = sub.head(cap)
    vals = list(vals)
    v = (s[vals].fillna("").astype(str).apply(lambda r: "; ".join(f"{c}={x}" for c, x in zip(vals, r)), axis=1)
         if vals else pd.Series("", index=s.index))
    out = pd.DataFrame({"file": file, "rule": rule, "category": cat, "key": ks(s, keys), "source_line": s["_ln"].values,
                        "values": v, "reason": reason, "total_cases_of_rule": n, "detail_file": detail}, index=s.index)
    if "A" in kind:
        ANOMP.append(out)
    if "R" in kind:
        r = out.copy()
        r["suggested_action"] = action or "Confirmar con el dueño del dato; se conserva el valor original."
        REVP.append(r)
    return n


def log_vec(file, df, mask, col, new, keys, reason):
    sub = df[mask]
    if sub.empty:
        return
    LOGP.append(pd.DataFrame({"file": file, "key": ks(sub, keys), "source_line": sub["_ln"].values, "column": col,
                              "old_value": sub[col].values, "new_value": new[mask].values, "reason": reason}))


def apply(df, name, col, new, reason):
    m = df[col].fillna("\0") != new.fillna("\0")
    if m.any():
        log_vec(name, df, m, col, new, KEYS.get(name, [df.columns[0]]), reason)
        df[col] = new


MASTER = {}


def fix_date(s):
    """Convierte a YYYY-MM-DD solo formatos de interpretación inequívoca (dd-Mon-yy; d/m/Y con día>12 o día=mes)."""
    out, v = s.copy(), s.fillna("")
    m1 = v.str.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{2}")
    if m1.any():
        p = pd.to_datetime(v[m1], format="%d-%b-%y", errors="coerce")
        out.loc[m1] = p.dt.strftime("%Y-%m-%d").fillna(v[m1])
    m2 = v.str.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}")
    if m2.any():
        a = v[m2].str.extract(r"(\d+)/(\d+)/(\d+)").astype(int)
        mdy = ((a[1] > 12) | (a[0] == a[1])) & (a[0] <= 12)
        dmy = (a[0] > 12) & (a[1] <= 12)
        for msk, fmt in ((mdy, "%m/%d/%Y"), (dmy, "%d/%m/%Y")):
            idx = a.index[msk]
            if len(idx):
                p = pd.to_datetime(v[idx], format=fmt, errors="coerce")
                out.loc[idx] = p.dt.strftime("%Y-%m-%d").fillna(v[idx])
    return out


def normalize(name, raw):
    df = raw.copy()
    for c in [c for c in df.columns if c != "_ln"]:
        s = df[c]
        apply(df, name, c, s.str.strip().replace("", np.nan), "Espacios sobrantes eliminados (formato; el valor no cambia)")
        s = df[c]
        apply(df, name, c, s.where(~s.isin(TOKENS), np.nan), "Marcador de faltante (p.ej. '#N/A') convertido a vacío; el dato sigue sin conocerse")
    for c in ("sku", "store_id", "site_id", "supplier_id", "promo_id"):
        if c in df.columns and MASTER.get(c):
            s = df[c]
            up = s.str.upper()
            apply(df, name, c, s.where(~(s.notna() & ~s.isin(MASTER[c]) & up.isin(MASTER[c])), up),
                  "Identificador en minúsculas normalizado a mayúsculas (existe en el maestro con ese formato)")
    for c in DATES.get(name, []):
        if c in df.columns:
            apply(df, name, c, fix_date(df[c]), "Fecha en formato no ISO convertida a YYYY-MM-DD (interpretación inequívoca; las ambiguas como 02/10/2026 NO se tocan)")
    if name == "prices":
        cur = df["currency"]
        apply(df, name, "currency", cur.str.upper().replace({"US$": "USD"}), "Moneda normalizada a 'USD' (misma moneda con distinto formato)")
    return df


def dedup(name, raw, norm):
    keys = KEYS.get(name, [raw.columns[0]])
    cols = [c for c in raw.columns if c != "_ln"]
    d_raw = raw.duplicated(cols, keep="first")
    d_norm = norm.duplicated(cols, keep="first")
    if d_norm.any():
        first = norm.groupby(cols, dropna=False, sort=False)["_ln"].transform("min")
        rem = norm[d_norm]
        typ = np.where(d_raw[d_norm], "Duplicado exacto", "Duplicado exacto tras normalizar formato")
        DUPP.append(pd.DataFrame({"file": name, "dup_type": typ, "key": ks(rem, keys), "source_line": rem["_ln"].values,
                                  "kept_line": first[d_norm].values, "differing_columns": "",
                                  "action": "Retirado de la copia limpia (se conserva la primera aparición); original intacto"}))
        LOGP.append(pd.DataFrame({"file": name, "key": ks(rem, keys), "source_line": rem["_ln"].values, "column": "(fila completa)",
                                  "old_value": "fila presente", "new_value": "fila retirada",
                                  "reason": "Duplicado exacto: todos los campos coinciden con la fila " + first[d_norm].astype(str).values}))
    clean = norm[~d_norm].copy()
    kd = clean.duplicated(keys, keep=False)
    if kd.any():
        g = clean[kd]
        nun = g.groupby(keys, dropna=False)[[c for c in cols if c not in keys]].nunique(dropna=False)
        diff = nun.apply(lambda r: ", ".join(r.index[r > 1]), axis=1)
        kk = ks(g, keys)
        gk = g.groupby(keys, dropna=False)
        lab = ks(nun.reset_index(), keys).values
        dm = dict(zip(lab, diff.values))
        DUPP.append(pd.DataFrame({"file": name, "dup_type": "Posible duplicado (misma clave, valores distintos)", "key": kk.values,
                                  "source_line": g["_ln"].values, "kept_line": g["_ln"].values,
                                  "differing_columns": [dm.get(k, "") for k in kk], "action": "Conservado; requiere revisión manual"}))
        first_of = g.drop_duplicates(keys)
        emit("R", name, "clave_duplicada_con_diferencias", "Posible duplicado",
             f"Varias filas comparten la clave de negocio {keys} con valores distintos; no se sabe cuál es correcta",
             first_of, pd.Series(True, index=first_of.index), keys,
             action="Decidir qué fila es válida (o si ambas son legítimas); ver duplicate_records.csv")
    return clean


def rel(src, col, tgt, tcol, df, ref):
    ok = set(ref.dropna())
    m = df[col].notna() & ~df[col].isin(ok)
    n = int(m.sum())
    RELP.append(dict(source_file=src, column=col, target_file=tgt, target_column=tcol, rows_checked=int(df[col].notna().sum()),
                     invalid_rows=n, distinct_invalid_values=int(df.loc[m, col].nunique()),
                     sample_invalid_values=", ".join(map(str, df.loc[m, col].drop_duplicates().head(10))),
                     status="REFERENCIAS INVÁLIDAS" if n else "OK"))
    emit("R", src, f"ref_invalida_{col}", "Inconsistencia relacional", f"{col} no existe en {tgt}.{tcol}",
         df, m, KEYS[src], [col], action="Confirmar si falta el registro maestro o si el identificador está mal capturado")
    return m


def main():
    if not RAW.exists():
        sys.exit(f"ERROR: no existe {RAW}. Coloca los CSV originales en data/raw/")
    for d in (CLEAN, REP):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    names = [n for n in FILES if (RAW / f"{n}.csv").exists()]
    extra = sorted(p.stem for p in RAW.glob("*.csv") if p.stem not in FILES)
    for n in FILES:
        if n not in names:
            print(f"AVISO: falta {n}.csv; se omite")
    if extra:
        print("Archivos adicionales detectados:", extra)
    names += extra
    hashes = {n: sha(RAW / f"{n}.csv") for n in names}
    R, C, S = {}, {}, []
    for n in names:
        try:
            d = pd.read_csv(RAW / f"{n}.csv", dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8-sig")
        except Exception as e:
            print(f"ERROR leyendo {n}: {e}")
            continue
        d["_ln"] = np.arange(2, len(d) + 2)
        R[n] = d
        print(f"Leído {n}: {d.shape[0]} filas")
    for k, f, c in (("sku", "products", "sku"), ("store_id", "stores", "store_id"), ("site_id", "sites", "site_id"),
                    ("supplier_id", "suppliers", "supplier_id"), ("promo_id", "promotions", "promo_id")):
        if f in R:
            MASTER[k] = set(R[f][c].dropna().str.strip())
    # ---------- limpieza base: normalizar + deduplicar exactos ----------
    for n, raw in R.items():
        KEYS.setdefault(n, [c for c in raw.columns if c != "_ln"][:1])
        C[n] = dedup(n, raw, normalize(n, raw))
        for c in raw.columns:
            if c == "_ln":
                continue
            blank, tok = int(raw[c].isna().sum()), int(raw[c].isin(TOKENS).sum())
            mc = int(C[n][c].isna().sum())
            MISSP.append(dict(file=n, column=c, missing_raw=blank, placeholder_tokens_raw=tok, missing_clean=mc,
                              pct_missing_clean=round(100 * mc / max(len(C[n]), 1), 3), interpretation=HINTS.get((n, c), "Sin interpretación documentada") if mc else ""))
        # genéricos: fechas, numéricos, constantes
        df = C[n]
        for c in DATES.get(n, []):
            if c in df:
                df["_d_" + c] = pdate(df[c])
                emit("AR", n, f"fecha_invalida_{c}", "Error confirmado", f"{c} no cumple formato YYYY-MM-DD o no es fecha real",
                     df, df[c].notna() & df["_d_" + c].isna(), KEYS[n], [c], action="Corregir fecha en la fuente; no se adivina")
        for c in NUMS.get(n, []):
            if c in df:
                emit("AR", n, f"no_numerico_{c}", "Error confirmado", f"{c} contiene texto no numérico",
                     df, df[c].notna() & num(df, c).isna(), KEYS[n], [c])
        for c in [c for c in df.columns if not c.startswith("_")]:
            if df[c].nunique(dropna=False) == 1 and len(df) > 1:
                RULES.append(dict(file=n, rule=f"columna_constante_{c}", category="Anomalía sospechosa", cases=len(df)))
                ANOMP.append(pd.DataFrame([dict(file=n, rule=f"columna_constante_{c}", category="Anomalía sospechosa", key="(columna)", source_line="",
                                                values=f"{c}={df[c].iloc[0]}", reason="Columna con valor único: aporta poca información (puede ser legítimo)", total_cases_of_rule=len(df), detail_file="")]))

    P, SA, INV, PR, PO, ST, SI, SU, K, W, PM, CL, CAL, BC = (C.get(k) for k in
        ["products", "sales_daily", "inventory_daily", "prices", "purchase_orders", "stores", "sites", "suppliers",
         "store_daily_kpis", "weather_daily", "promotions", "store_closures", "calendar", "product_barcodes"])
    g = lambda d, c: d[c] if d is not None and c in d else pd.Series([], dtype=str)
    skus = set(g(P, "sku")); stores = set(g(ST, "store_id")); sites = set(g(SI, "site_id"))
    sups = set(g(SU, "supplier_id")); promos = set(g(PM, "promo_id"))
    cal_min = cal_max = None
    if CAL is not None and CAL["_d_date"].notna().any():
        cal_min, cal_max = CAL["_d_date"].min(), CAL["_d_date"].max()
    for n in ("sales_daily", "inventory_daily", "store_daily_kpis", "weather_daily"):
        pass

    # ================= CALENDAR =================
    if CAL is not None:
        d = CAL.dropna(subset=["_d_date"])
        full = pd.date_range(cal_min, cal_max)
        miss = sorted(set(full) - set(d["_d_date"]))
        if miss:
            MISSP.append(dict(file="calendar", column="(fechas)", missing_raw=len(miss), placeholder_tokens_raw=0, missing_clean=len(miss), pct_missing_clean=0, interpretation="Fechas ausentes en el rango"))
        dd = d["_d_date"]
        emit("AR", "calendar", "dia_semana_inconsistente", "Error confirmado", "day_of_week no corresponde a la fecha", d, d["day_of_week"] != dd.dt.strftime("%a"), ["date"], ["day_of_week"])
        emit("AR", "calendar", "fin_semana_inconsistente", "Error confirmado", "is_weekend no corresponde a la fecha", d, num(d, "is_weekend") != (dd.dt.dayofweek >= 5).astype(int), ["date"], ["is_weekend"])
        emit("AR", "calendar", "iso_week_inconsistente", "Error confirmado", "iso_week no corresponde a la fecha", d, num(d, "iso_week") != dd.dt.isocalendar().week.astype(float), ["date"], ["iso_week"])
        emit("AR", "calendar", "mes_anio_inconsistente", "Error confirmado", "month/year no corresponden a la fecha", d, (num(d, "month") != dd.dt.month) | (num(d, "year") != dd.dt.year), ["date"], ["month", "year"])
        emit("AR", "calendar", "feriado_sin_nombre", "Anomalía sospechosa", "is_holiday=1 sin holiday_name o viceversa", d, (num(d, "is_holiday") == 1) != d["holiday_name"].notna(), ["date"], ["is_holiday", "holiday_name"])
        emit("A", "calendar", "quarter_idx_no_calendario", "Dato válido que debe conservarse",
             "quarter_idx no coincide con el trimestre calendario (parece índice fiscal/relativo; no confirmado)", d, num(d, "quarter_idx") != dd.dt.quarter, ["date"], ["quarter_idx"], cap=5)

    # ================= PRODUCTS =================
    if P is not None:
        launch, disc = P["_d_launch_date"], P["_d_discontinued_date"]
        emit("AR", "products", "descontinuado_antes_de_lanzamiento", "Anomalía sospechosa", "discontinued_date anterior a launch_date; no se sabe cuál fecha es la errónea", P, disc < launch, ["sku"], ["launch_date", "discontinued_date", "status"])
        uc, lp = num(P, "unit_cost"), num(P, "list_price_base")
        emit("AR", "products", "unit_cost_faltante", "Dato faltante", "unit_cost sin valor (no se imputa)", P, P["unit_cost"].isna(), ["sku"], ["product_name", "unit_cost"])
        emit("AR", "products", "costo_precio_cero_o_negativo", "Anomalía sospechosa", "Costo o precio <= 0 (puede ser cortesía/modificador gratuito)", P, (uc <= 0) | (lp <= 0), ["sku"], ["product_name", "unit_cost", "list_price_base", "department"])
        emit("AR", "products", "precio_menor_que_costo", "Anomalía sospechosa", "list_price_base < unit_cost (margen negativo)", P, (lp < uc) & (lp > 0), ["sku"], ["unit_cost", "list_price_base"])
        sl, per = num(P, "shelf_life_days"), num(P, "is_perishable")
        emit("AR", "products", "shelf_life_negativo", "Error confirmado", "shelf_life_days negativo es imposible; valor correcto desconocido", P, sl < 0, ["sku"], ["shelf_life_days", "is_perishable"], action="Obtener la vida útil real; no se corrige automáticamente")
        emit("AR", "products", "shelf_life_cero", "Anomalía sospechosa", "shelf_life_days = 0 (¿vida útil de 0 días?)", P, sl == 0, ["sku"], ["shelf_life_days", "is_perishable"])
        emit("AR", "products", "perecedero_sin_shelf_life", "Dato faltante", "Producto perecedero sin shelf_life_days", P, (per == 1) & sl.isna(), ["sku"], ["is_perishable", "shelf_life_days"])
        st = P["status"]
        emit("AR", "products", "estado_vs_fecha_descontinuacion", "Anomalía sospechosa", "status=Discontinued sin fecha, o Active con discontinued_date", P, ((st == "Discontinued") & disc.isna()) | ((st == "Active") & disc.notna()), ["sku"], ["status", "discontinued_date"])
        rel("products", "supplier_id", "suppliers", "supplier_id", P, pd.Series(list(sups)))
        if BC is not None:
            emit("A", "products", "sku_sin_codigo_de_barras", "Dato faltante", "SKU sin registro en product_barcodes (no se exige uno por SKU)", P, ~P["sku"].isin(set(BC["sku"])), ["sku"], ["product_name", "department"], cap=50)
        # conflictos de SKU
        dsk = P[P["sku"].duplicated(keep=False)]
        po_cost = PO.groupby("sku")["unit_cost"].agg(lambda s: set(s.dropna())) if PO is not None else {}
        pr_p = PR.groupby("sku")["unit_price"].agg(lambda s: set(s.dropna())) if PR is not None else {}
        sa_p = SA[SA["sku"].isin(set(dsk["sku"]))].groupby("sku")["unit_price"].agg(lambda s: set(s.dropna())) if SA is not None else {}
        cols = [c for c in P.columns if not c.startswith("_") and c != "sku"]
        raw_dups = R["products"]["sku"].duplicated(keep=False)
        n_identical = 0
        for sku, gp in dsk.groupby("sku"):
            diff = [c for c in cols if gp[c].nunique(dropna=False) > 1]
            ev = []
            if "unit_cost" in diff:
                ref = {float(x) for x in po_cost.get(sku, set())} if len(po_cost) else set()
                ev.append("unit_cost vs purchase_orders: " + "; ".join(f"{v}={'coincide' if v is not None and not pd.isna(v) and float(v) in ref else 'sin evidencia'}" for v in gp["unit_cost"]))
            if "list_price_base" in diff:
                ref = {float(x) for x in (pr_p.get(sku, set()) if len(pr_p) else set()) | (sa_p.get(sku, set()) if len(sa_p) else set())}
                ev.append("list_price_base vs prices/sales: " + "; ".join(f"{v}={'coincide' if v is not None and not pd.isna(v) and float(v) in ref else 'sin evidencia'}" for v in gp["list_price_base"]))
            for c in diff:
                if c in ("launch_date", "discontinued_date") and "discontinued_date" in diff:
                    pass
            valid = None
            if diff and any("coincide" in e for e in ev):
                valid = [e for e in ev]
            rec = ("Candidato con evidencia parcial; confirmar manualmente y conservar el resto hasta confirmar" if valid and not all("sin evidencia" not in e or "coincide" in e for e in [])
                   else "Sin evidencia suficiente en los CSV: conservar todas las filas y revisar manualmente")
            SKUP.append(dict(sku=sku, n_rows=len(gp), source_lines="; ".join(map(str, gp["_ln"])), differing_columns=", ".join(diff),
                             conflicting_values=" || ".join(f"{c}: " + " | ".join(map(lambda x: "NaN" if pd.isna(x) else str(x), gp[c])) for c in diff),
                             cross_evidence=" / ".join(ev) or "n/d", barcodes_in_product_barcodes=int((BC["sku"] == sku).sum()) if BC is not None else 0,
                             classification="Posible duplicado", recommendation=rec))
        rawd = R["products"][R["products"]["sku"].duplicated(keep=False)]
        ident = [s for s, gp in rawd.groupby("sku") if len(gp.drop(columns="_ln").drop_duplicates()) == 1]
        for s in ident:
            SKUP.append(dict(sku=s, n_rows=int((rawd["sku"] == s).sum()), source_lines="; ".join(map(str, rawd.loc[rawd["sku"] == s, "_ln"])),
                             differing_columns="", conflicting_values="", cross_evidence="n/a", barcodes_in_product_barcodes=0,
                             classification="Duplicado exacto", recommendation="Se conserva una sola fila en la copia limpia (acción aplicada)"))
        if len(dsk):
            RULES.append(dict(file="products", rule="sku_repetido_con_diferencias", category="Posible duplicado", cases=int(dsk["sku"].nunique())))
        emit("R", "products", "sku_repetido_con_diferencias_lista", "Posible duplicado", "SKU repetido con valores distintos: ver product_sku_conflicts.csv", dsk.drop_duplicates("sku"), pd.Series(True, index=dsk.drop_duplicates("sku").index), ["sku"], action="Ver product_sku_conflicts.csv")
        RULES.pop()  # evita doble conteo

    # ================= BARCODES / SUPPLIERS =================
    if BC is not None:
        rel("product_barcodes", "sku", "products", "sku", BC, pd.Series(list(skus)))
        emit("AR", "product_barcodes", "barcode_en_varios_sku", "Posible duplicado", "Un mismo código de barras apunta a varios SKU", BC, BC["barcode"].duplicated(keep=False), ["barcode"], ["sku"])
        mult = BC["sku"].duplicated(keep=False)
        emit("A", "product_barcodes", "sku_con_varios_barcodes", "Dato válido que debe conservarse", "Un SKU puede tener varios códigos (relación 1:1 no exigida)", BC, mult, ["barcode"], ["sku", "barcode_type"], cap=50)
        ln = BC["barcode"].str.len()
        bad = (~BC["barcode"].str.fullmatch(r"\d+")) | ((BC["barcode_type"] == "UPC-A") & (ln != 12)) | ((BC["barcode_type"] == "EAN-13") & (ln != 13))
        emit("AR", "product_barcodes", "barcode_formato_vs_tipo", "Anomalía sospechosa", "Longitud/dígitos no coinciden con barcode_type", BC, bad, ["barcode"], ["barcode_type"])

        def chk(code):
            if not code.isdigit() or len(code) < 2:
                return False
            d = [int(x) for x in code]
            tot = sum(x * (3 if (len(d) - 2 - i) % 2 == 0 else 1) for i, x in enumerate(d[:-1]))
            return (10 - tot % 10) % 10 == d[-1]
        okd = BC["barcode"].map(chk)
        emit("A", "product_barcodes", "digito_verificador_invalido", "Anomalía sospechosa", "El dígito verificador GS1 no cuadra (dataset sintético: puede ser esperado)", BC, ~okd & ~bad, ["barcode"], ["barcode_type"], cap=50)
    if SU is not None:
        for c in ("contract_fill_rate", "contract_on_time_rate"):
            emit("AR", "suppliers", f"{c}_fuera_de_0_1", "Error confirmado", "Tasa fuera de [0,1]", SU, (num(SU, c) < 0) | (num(SU, c) > 1), ["supplier_id"], [c])
        emit("AR", "suppliers", "lead_time_o_minimo_no_positivo", "Anomalía sospechosa", "Lead time <= 0 o monto mínimo negativo", SU, (num(SU, "standard_lead_time_days") <= 0) | (num(SU, "min_order_value") < 0), ["supplier_id"], ["standard_lead_time_days", "min_order_value"])

    # ================= SITES / STORES / CLOSURES =================
    if ST is not None:
        rel("stores", "site_id", "sites", "site_id", ST, pd.Series(list(sites)))
        emit("AR", "stores", "horario_faltante", "Dato faltante", "opening_time o closing_time sin valor (incluye '#N/A' original); no se imputa", ST, ST["opening_time"].isna() | ST["closing_time"].isna(), ["store_id"], ["opening_time", "closing_time"], action="Obtener el horario real")
        emit("A", "stores", "horario_24h_00_00", "Dato válido que debe conservarse", "00:00/00:00 significa 24 horas según README", ST, (ST["opening_time"] == "00:00") & (ST["closing_time"] == "00:00"), ["store_id"], ["opening_time", "closing_time"], cap=5)
        emit("AR", "stores", "cierre_antes_de_apertura", "Error confirmado", "closed_date < opened_date", ST, ST["_d_closed_date"] < ST["_d_opened_date"], ["store_id"], ["opened_date", "closed_date"])
        emit("AR", "stores", "sqft_no_positivo", "Anomalía sospechosa", "sqft <= 0", ST, num(ST, "sqft") <= 0, ["store_id"], ["sqft"])
        emit("AR", "stores", "full_counter_sin_asientos", "Anomalía sospechosa", "seats = 0 en Full Counter (en kioscos/shop-in-shop 0 es plausible)", ST, (num(ST, "seats") == 0) & (ST["format"] == "Full Counter"), ["store_id"], ["format", "seats"])
        emit("A", "stores", "nombre_con_formato_irregular", "Anomalía sospechosa", "store_name con mayúsculas/prefijo inconsistentes (cosmético)", ST, ST["store_name"].str.contains(r"^[a-z]", regex=True), ["store_id"], ["store_name"], cap=50)
        emit("A", "stores", "tienda_sin_ventas", "Inconsistencia relacional", "store_id sin ventas en sales_daily (README habla de 38 tiendas)", ST, ~ST["store_id"].isin(set(g(SA, "store_id"))) if SA is not None else pd.Series(False, index=ST.index), ["store_id"], ["store_name", "opened_date", "closed_date"])
    if SI is not None:
        emit("AR", "sites", "tier_no_positivo", "Anomalía sospechosa", "traffic_tier <= 0", SI, num(SI, "traffic_tier") <= 0, ["site_id"], ["traffic_tier"])
    if CL is not None:
        rel("store_closures", "store_id", "stores", "store_id", CL, pd.Series(list(stores)))
        emit("AR", "store_closures", "fin_antes_de_inicio", "Error confirmado", "end_date < start_date", CL, CL["_d_end_date"] < CL["_d_start_date"], ["store_id"], ["start_date", "end_date"])
        emit("A", "store_closures", "cierre_de_un_dia", "Dato válido que debe conservarse", "start_date = end_date (cierre de un día; común en cierres definitivos)", CL, CL["_d_end_date"] == CL["_d_start_date"], ["store_id"], ["start_date", "end_date", "reason"])
        if ST is not None:
            perm = CL[CL["reason"] == "Permanent closure"].merge(ST[["store_id", "closed_date"]], on="store_id", how="left", suffixes=("", "_st"))
            perm["_ln"] = perm["_ln"]
            emit("AR", "store_closures", "cierre_definitivo_vs_stores", "Inconsistencia relacional", "Permanent closure sin closed_date coincidente en stores", perm, perm["closed_date"] != perm["start_date"], ["store_id"], ["start_date", "closed_date"])
            cl2 = ST[ST["closed_date"].notna()].merge(CL[CL["reason"] == "Permanent closure"][["store_id"]], on="store_id", how="left", indicator=True)
            emit("AR", "stores", "closed_date_sin_cierre_definitivo", "Inconsistencia relacional", "stores.closed_date sin 'Permanent closure' en store_closures", cl2, cl2["_merge"] == "left_only", ["store_id"], ["closed_date"])
        o = CL.sort_values(["store_id", "_d_start_date"])
        ov = (o.groupby("store_id")["_d_start_date"].shift(-1) <= o["_d_end_date"])
        emit("AR", "store_closures", "ventanas_traslapadas", "Anomalía sospechosa", "Ventanas de cierre traslapadas para la misma tienda", o, ov, ["store_id"], ["start_date", "end_date"])

    # ================= PRICES =================
    if PR is not None:
        rel("prices", "store_id", "stores", "store_id", PR, pd.Series(list(stores)))
        rel("prices", "sku", "products", "sku", PR, pd.Series(list(skus)))
        up = num(PR, "unit_price")
        emit("AR", "prices", "precio_faltante", "Dato faltante", "unit_price sin valor; no se imputa", PR, PR["unit_price"].isna(), ["store_id", "sku", "effective_from"], ["unit_price", "price_id"])
        emit("AR", "prices", "precio_cero_o_negativo", "Anomalía sospechosa", "unit_price <= 0", PR, up <= 0, ["store_id", "sku", "effective_from"], ["unit_price"])
        emit("AR", "prices", "vigencia_invertida", "Error confirmado", "effective_to anterior a effective_from", PR, PR["_d_effective_to"] < PR["_d_effective_from"], ["store_id", "sku", "effective_from"], ["effective_from", "effective_to", "unit_price"], action="Verificar fechas de vigencia reales")
        emit("AR", "prices", "price_id_duplicado", "Posible duplicado", "price_id repetido con filas distintas", PR, PR["price_id"].duplicated(keep=False), ["price_id"], ["store_id", "sku", "unit_price"])
        o = PR.sort_values(["store_id", "sku", "_d_effective_from", "_ln"]).copy()
        gk = o.groupby(["store_id", "sku"])
        nxt = gk["_d_effective_from"].shift(-1)
        prev_to = o["_d_effective_to"]
        o["_ov"] = nxt.notna() & (prev_to.isna() | (prev_to >= nxt)) & (o["_d_effective_to"].notna() | nxt.notna())
        emit("AR", "prices", "vigencias_traslapadas", "Anomalía sospechosa", "Periodos de precio traslapados (o precio abierto seguido de otro) para la misma tienda/SKU", o, o["_ov"], ["store_id", "sku", "effective_from"], ["effective_from", "effective_to", "unit_price"])
        gap = nxt.notna() & prev_to.notna() & ((prev_to + pd.Timedelta(days=1)) < nxt)
        emit("A", "prices", "hueco_entre_vigencias", "Anomalía sospechosa", "Hueco sin precio entre dos vigencias", o, gap, ["store_id", "sku", "effective_from"], ["effective_to"], cap=100)
        if P is not None:
            lpb = P.drop_duplicates("sku").set_index("sku")["list_price_base"].astype(float, errors="ignore")
            ratio = up / PR["sku"].map(pd.to_numeric(lpb, errors="coerce"))
            emit("AR", "prices", "precio_vs_base_extremo", "Anomalía sospechosa", "unit_price < 0.5x o > 2x list_price_base", PR, (ratio < 0.5) | (ratio > 2), ["store_id", "sku", "effective_from"], ["unit_price"])

    # ================= PROMOTIONS =================
    if PM is not None:
        emit("AR", "promotions", "inicio_posterior_a_fin", "Error confirmado", "start_date > end_date; no se inventa la fecha correcta", PM, PM["_d_start_date"] > PM["_d_end_date"], ["promo_id"], ["promo_name", "start_date", "end_date"], action="Confirmar fechas reales (¿intercambiadas o mal escritas?)")
        emit("A", "promotions", "categoria_vacia", "Dato válido que debe conservarse", "Sin categoría = alcance a nivel departamento", PM, PM["category"].isna(), ["promo_id"], ["department"], cap=20)
        emit("AR", "promotions", "descuento_fuera_de_rango", "Error confirmado", "discount_pct fuera de (0,100]", PM, (num(PM, "discount_pct") <= 0) | (num(PM, "discount_pct") > 100), ["promo_id"], ["discount_pct"])
        if P is not None:
            rel("promotions", "department", "products", "department", PM, P["department"])
        if SI is not None:
            m = ~PM["region_scope"].isin(set(SI["region"]) | {"ALL"})
            emit("AR", "promotions", "region_inexistente", "Inconsistencia relacional", "region_scope no es ALL ni región de sites", PM, m, ["promo_id"], ["region_scope"])

    # ================= PURCHASE ORDERS =================
    if PO is not None:
        rel("purchase_orders", "store_id", "stores", "store_id", PO, pd.Series(list(stores)))
        rel("purchase_orders", "sku", "products", "sku", PO, pd.Series(list(skus)))
        rel("purchase_orders", "supplier_id", "suppliers", "supplier_id", PO, pd.Series(list(sups)))
        qo, qr, od, ed, rd = num(PO, "qty_ordered"), num(PO, "qty_received"), PO["_d_order_date"], PO["_d_expected_date"], PO["_d_received_date"]
        K3 = ["po_id", "store_id", "sku"]
        emit("AR", "purchase_orders", "expected_date_faltante", "Dato faltante", "Sin fecha esperada; no se imputa", PO, PO["expected_date"].isna(), K3, ["order_date", "status"])
        emit("A", "purchase_orders", "recepcion_faltante_en_transito", "Dato válido que debe conservarse", "In transit sin recepción: pendiente, no es 0", PO, (PO["status"] == "In transit") & PO["received_date"].isna() & PO["qty_received"].isna(), K3, ["status"], cap=20)
        emit("AR", "purchase_orders", "recepcion_faltante_no_transito", "Dato faltante", "Delivered/Short-shipped sin received_date o qty_received", PO, (PO["status"] != "In transit") & (PO["status"] != "Cancelled") & (PO["received_date"].isna() | PO["qty_received"].isna()), K3, ["status", "received_date", "qty_received"])
        emit("AR", "purchase_orders", "recepcion_en_transito", "Anomalía sospechosa", "In transit pero con fecha/cantidad de recepción", PO, (PO["status"] == "In transit") & (PO["received_date"].notna() | PO["qty_received"].notna()), K3, ["status", "received_date", "qty_received"])
        emit("AR", "purchase_orders", "esperada_antes_de_pedido", "Error confirmado", "expected_date < order_date", PO, ed < od, K3, ["order_date", "expected_date"])
        emit("AR", "purchase_orders", "recibida_antes_de_pedido", "Error confirmado", "received_date < order_date", PO, rd < od, K3, ["order_date", "received_date"])
        emit("A", "purchase_orders", "recibida_tarde", "Dato válido que debe conservarse", "Recepción posterior a lo esperado (retraso de proveedor, legítimo)", PO, rd > ed, K3, ["expected_date", "received_date"], cap=20)
        emit("AR", "purchase_orders", "recibido_mayor_que_pedido", "Anomalía sospechosa", "qty_received > qty_ordered", PO, qr > qo, K3, ["qty_ordered", "qty_received"])
        emit("AR", "purchase_orders", "cantidad_pedida_no_positiva", "Error confirmado", "qty_ordered <= 0", PO, qo <= 0, K3, ["qty_ordered"])
        emit("AR", "purchase_orders", "delivered_incompleto", "Anomalía sospechosa", "Delivered con qty_received < qty_ordered", PO, (PO["status"] == "Delivered") & (qr < qo), K3, ["qty_ordered", "qty_received"])
        emit("AR", "purchase_orders", "short_shipped_completo", "Anomalía sospechosa", "Short-shipped con qty_received >= qty_ordered", PO, (PO["status"] == "Short-shipped") & (qr >= qo), K3, ["qty_ordered", "qty_received"])
        emit("AR", "purchase_orders", "unit_cost_faltante", "Dato faltante", "unit_cost vacío; no se imputa", PO, PO["unit_cost"].isna(), K3, ["unit_cost"])
        if P is not None:
            pp = P.drop_duplicates("sku", keep=False).set_index("sku")
            cs = PO["sku"].map(pd.to_numeric(pp["case_size"], errors="coerce"))
            emit("A", "purchase_orders", "no_multiplo_case_size", "Anomalía sospechosa", "qty_ordered no es múltiplo de case_size (README)", PO, (cs > 0) & ((qo % cs) != 0), K3, ["qty_ordered"])
            sp = PO["sku"].map(pp["supplier_id"])
            emit("A", "purchase_orders", "proveedor_distinto_al_maestro", "Inconsistencia relacional", "supplier_id de la OC difiere de products.supplier_id", PO, sp.notna() & (sp != PO["supplier_id"]), K3, ["supplier_id"])
            uc = PO["sku"].map(pd.to_numeric(pp["unit_cost"], errors="coerce"))
            cr = num(PO, "unit_cost") / uc
            emit("A", "purchase_orders", "costo_vs_maestro_extremo", "Anomalía sospechosa", "unit_cost de OC <0.5x o >2x products.unit_cost", PO, (cr < 0.5) | (cr > 2), K3, ["unit_cost"])
            emit("A", "purchase_orders", "sku_sin_control_inventario", "Inconsistencia relacional", "OC de SKU con inventory_tracked=0", PO, PO["sku"].map(pp["inventory_tracked"]) == "0", K3, ["sku"])
        if cal_max is not None:
            emit("A", "purchase_orders", "en_transito_antiguo", "Anomalía sospechosa", "In transit con >30 días desde el pedido hasta el fin del calendario", PO, (PO["status"] == "In transit") & (od < cal_max - pd.Timedelta(days=30)), K3, ["order_date"])

    # ================= SALES =================
    if SA is not None:
        SA["_d"] = SA["_d_date"]
        un, up, gs, da, ns = (num(SA, c) for c in ("units_sold", "unit_price", "gross_sales", "discount_amount", "net_sales"))
        K3 = ["date", "store_id", "sku"]
        rel("sales_daily", "store_id", "stores", "store_id", SA, pd.Series(list(stores)))
        rel("sales_daily", "sku", "products", "sku", SA, pd.Series(list(skus)))
        rel("sales_daily", "promo_id", "promotions", "promo_id", SA, pd.Series(list(promos)))
        emit("AR", "sales_daily", "units_sold_faltante", "Dato faltante", "units_sold vacío (no es 0). Podría derivarse de gross_sales/unit_price pero no se aplica automáticamente", SA, SA["units_sold"].isna(), K3, ["unit_price", "gross_sales", "net_sales"], action="Confirmar si se acepta units = gross_sales/unit_price")
        emit("AR", "sales_daily", "unidades_negativas", "Anomalía sospechosa", "Cantidad negativa: posible devolución/ajuste (no se elimina)", SA, un < 0, K3, ["units_sold", "gross_sales", "net_sales"])
        emit("A", "sales_daily", "unidades_cero", "Dato válido que debe conservarse", "units_sold = 0 es una observación válida", SA, un == 0, K3, ["units_sold"], cap=20)
        emit("AR", "sales_daily", "precio_no_positivo_o_faltante", "Anomalía sospechosa", "unit_price <= 0 o vacío", SA, (up <= 0) | up.isna(), K3, ["unit_price"])
        emit("AR", "sales_daily", "bruto_distinto_unidades_por_precio", "Inconsistencia relacional", "gross_sales != units_sold*unit_price (tol. 0.011)", SA, ((un * up) - gs).abs() > 0.011, K3, ["units_sold", "unit_price", "gross_sales"])
        emit("AR", "sales_daily", "neto_distinto_bruto_menos_descuento", "Inconsistencia relacional", "net_sales != gross_sales - discount_amount", SA, ((gs - da) - ns).abs() > 0.011, K3, ["gross_sales", "discount_amount", "net_sales"])
        emit("AR", "sales_daily", "descuento_negativo_o_mayor_que_bruto", "Anomalía sospechosa", "discount_amount < 0 o > gross_sales (en positivos)", SA, (da < 0) | ((da > gs) & (gs > 0)), K3, ["gross_sales", "discount_amount"])
        emit("AR", "sales_daily", "promo_sin_descuento", "Anomalía sospechosa", "Fila con promo_id y discount_amount = 0", SA, SA["promo_id"].notna() & (da == 0), K3, ["promo_id", "discount_amount"])
        emit("A", "sales_daily", "fecha_fuera_de_calendario", "Inconsistencia relacional", "Fecha fuera del rango de calendar.csv", SA, SA["_d"].notna() & (cal_min is not None) & ((SA["_d"] < cal_min) | (SA["_d"] > cal_max)), K3, ["date"])
        if CAL is not None:
            emit("AR", "sales_daily", "fecha_inexistente_en_calendario", "Inconsistencia relacional", "Fecha ausente en calendar", SA, SA["_d"].notna() & ~SA["date"].isin(set(CAL["date"])), K3, ["date"])
        q = SA.assign(_u=un).groupby("sku")["_u"]
        q1, q3 = q.transform(lambda s: s.quantile(0.25)), q.transform(lambda s: s.quantile(0.75))
        emit("A", "sales_daily", "unidades_extremas", "Anomalía sospechosa", "units_sold > Q3 + 5*IQR del SKU (y >=20); inusual pero no se elimina", SA, (un > q3 + 5 * (q3 - q1)) & (un >= 20), K3, ["units_sold"])
        if PM is not None:
            pm = SA[SA["promo_id"].notna()].merge(PM[["promo_id", "_d_start_date", "_d_end_date"]], on="promo_id", how="inner")
            emit("AR", "sales_daily", "promo_fuera_de_vigencia", "Anomalía sospechosa", "Venta con promo_id fuera de start/end (promos con fechas invertidas se excluyen)", pm, (pm["_d_start_date"] <= pm["_d_end_date"]) & ((pm["_d"] < pm["_d_start_date"]) | (pm["_d"] > pm["_d_end_date"])), K3, ["promo_id", "date"])
            emit("AR", "sales_daily", "promo_con_fechas_invalidas", "Inconsistencia relacional", "Venta con promo cuya vigencia es inconsistente (start>end)", pm, pm["_d_start_date"] > pm["_d_end_date"], K3, ["promo_id", "date"], cap=100)
        if CL is not None:
            for _, c in CL.iterrows():
                if pd.isna(c["_d_start_date"]):
                    continue
                m = (SA["store_id"] == c["store_id"]) & (SA["_d"] >= c["_d_start_date"]) & (SA["_d"] <= c["_d_end_date"])
                emit("AR", "sales_daily", f"venta_en_cierre_{c['store_id']}", "Anomalía sospechosa", f"Venta dentro de ventana de cierre ({c['reason']} {c['start_date']}..{c['end_date']}); ¿fechas de cierre o ventas erróneas?", SA, m, K3, ["units_sold", "net_sales"])
        if ST is not None:
            s2 = SA.merge(ST[["store_id", "_d_opened_date", "_d_closed_date"]], on="store_id", how="left")
            emit("AR", "sales_daily", "venta_antes_de_apertura", "Anomalía sospechosa", "Venta anterior a opened_date", s2, s2["_d"] < s2["_d_opened_date"], K3, ["date"])
            emit("AR", "sales_daily", "venta_despues_de_cierre_definitivo", "Anomalía sospechosa", "Venta posterior a closed_date", s2, s2["_d"] > s2["_d_closed_date"], K3, ["date"])
        if P is not None:
            pl = P.drop_duplicates("sku", keep=False).set_index("sku")
            ld = SA["sku"].map(pl["_d_launch_date"])
            emit("A", "sales_daily", "venta_antes_de_lanzamiento", "Anomalía sospechosa", "Venta anterior a launch_date del producto", SA, SA["_d"] < ld, K3, ["date"], cap=200)

    # ================= INVENTORY =================
    if INV is not None:
        K3 = ["date", "store_id", "sku"]
        rel("inventory_daily", "store_id", "stores", "store_id", INV, pd.Series(list(stores)))
        rel("inventory_daily", "sku", "products", "sku", INV, pd.Series(list(skus)))
        v = {c: num(INV, c) for c in NUMS["inventory_daily"]}
        calc = v["opening_qty"] + v["received_qty"] - v["units_sold"] - v["expired_qty"] - v["damaged_qty"]
        diff = v["closing_qty"] - calc
        INV["_diff"] = diff
        emit("AR", "inventory_daily", "ecuacion_inventario_no_cuadra", "Inconsistencia relacional",
             "closing_qty != opening+received-sold-expired-damaged. Puede haber ajustes/transferencias no registrados; no se corrige", INV, diff.abs() > 1e-9, K3, ["opening_qty", "received_qty", "units_sold", "expired_qty", "damaged_qty", "closing_qty", "_diff"])
        neg = (v["opening_qty"] < 0) | (v["received_qty"] < 0) | (v["units_sold"] < 0) | (v["expired_qty"] < 0) | (v["damaged_qty"] < 0) | (v["closing_qty"] < 0) | (v["on_order_qty"] < 0)
        emit("AR", "inventory_daily", "cantidad_negativa", "Anomalía sospechosa", "Alguna cantidad negativa (posible ajuste)", INV, neg, K3, ["opening_qty", "received_qty", "units_sold", "closing_qty"])
        emit("AR", "inventory_daily", "stockout_hours_fuera_de_0_24", "Error confirmado", "stockout_hours <0 o >24", INV, (v["stockout_hours"] < 0) | (v["stockout_hours"] > 24), K3, ["stockout_hours"])
        if P is not None:
            tr = P.drop_duplicates("sku", keep=False).set_index("sku")["inventory_tracked"]
            emit("AR", "inventory_daily", "sku_sin_control_inventario", "Inconsistencia relacional", "SKU con inventory_tracked=0 aparece en inventory_daily", INV, INV["sku"].map(tr) == "0", K3, ["sku"])
        o = INV.dropna(subset=["_d_date"]).sort_values(["store_id", "sku", "_d_date"]).copy()
        o["_pc"] = o.groupby(["store_id", "sku"])["closing_qty"].shift(1)
        o["_pd"] = o.groupby(["store_id", "sku"])["_d_date"].shift(1)
        brk = (o["_d_date"] - o["_pd"] == pd.Timedelta(days=1)) & (pd.to_numeric(o["_pc"], errors="coerce") != num(o, "opening_qty"))
        emit("A", "inventory_daily", "continuidad_apertura_vs_cierre_previo", "Anomalía sospechosa", "opening_qty != closing_qty del día anterior (puede haber recepciones/ajustes nocturnos)", o, brk, K3, ["opening_qty", "_pc"])
        if SA is not None:
            sk = SA[~SA.duplicated(K3, keep=False)][K3 + ["units_sold"]].rename(columns={"units_sold": "_su"})
            m = INV[~INV.duplicated(K3, keep=False)].merge(sk, on=K3, how="inner")
            emit("AR", "inventory_daily", "unidades_vs_sales_daily", "Inconsistencia relacional", "units_sold de inventario difiere de sales_daily para la misma clave", m, num(m, "units_sold") != num(m, "_su"), K3, ["units_sold", "_su"])

    # ================= KPIs =================
    if K is not None:
        K2 = ["store_id", "date"]
        rel("store_daily_kpis", "store_id", "stores", "store_id", K, pd.Series(list(stores)))
        ff, tx = num(K, "footfall"), num(K, "transactions")
        emit("AR", "store_daily_kpis", "footfall_faltante", "Dato faltante", "footfall vacío (no es 0); no se imputa", K, K["footfall"].isna(), K2, ["transactions"])
        for c in ("footfall", "transactions", "units_sold", "net_sales", "staff_hours", "open_hours"):
            emit("AR", "store_daily_kpis", f"{c}_negativo", "Anomalía sospechosa", f"{c} < 0", K, num(K, c) < 0, K2, [c])
        emit("AR", "store_daily_kpis", "transacciones_mayor_que_afluencia", "Anomalía sospechosa", "transactions > footfall (el contador de afluencia puede subestimar)", K, tx > ff, K2, ["footfall", "transactions"])
        emit("A", "store_daily_kpis", "afluencia_cero", "Dato válido que debe conservarse", "footfall = 0 es un valor real (p.ej. tienda cerrada)", K, ff == 0, K2, ["footfall", "net_sales"], cap=20)
        emit("AR", "store_daily_kpis", "open_hours_fuera_de_0_24", "Error confirmado", "open_hours <0 o >24", K, (num(K, "open_hours") < 0) | (num(K, "open_hours") > 24), K2, ["open_hours"])
        for c in ("footfall", "net_sales", "staff_hours"):
            x = num(K, c)
            gq = K.assign(_x=x).groupby("store_id")["_x"]
            q1, q3 = gq.transform(lambda s: s.quantile(.25)), gq.transform(lambda s: s.quantile(.75))
            emit("A", "store_daily_kpis", f"{c}_extremo", "Anomalía sospechosa", f"{c} > Q3+5*IQR de la tienda", K, x > q3 + 5 * (q3 - q1), K2, [c])
        if CL is not None:
            for _, c in CL.iterrows():
                if pd.isna(c["_d_start_date"]):
                    continue
                m = (K["store_id"] == c["store_id"]) & (K["_d_date"] >= c["_d_start_date"]) & (K["_d_date"] <= c["_d_end_date"]) & (num(K, "net_sales") > 0)
                emit("AR", "store_daily_kpis", f"kpi_con_ventas_en_cierre_{c['store_id']}", "Anomalía sospechosa", f"net_sales>0 durante cierre {c['reason']}", K, m, K2, ["net_sales", "open_hours"])
        if SA is not None:
            ag = SA.assign(_u=num(SA, "units_sold"), _n=num(SA, "net_sales")).groupby(["store_id", "date"])[["_u", "_n"]].sum(min_count=1).reset_index()
            m = K[~K.duplicated(K2, keep=False)].merge(ag, on=K2, how="inner")
            emit("AR", "store_daily_kpis", "kpi_vs_sales_daily", "Inconsistencia relacional", "units_sold/net_sales del KPI difieren de la suma de sales_daily (unidades exactas, ventas tol. 0.5)", m, (num(m, "units_sold") != m["_u"]) | ((num(m, "net_sales") - m["_n"]).abs() > 0.5), K2, ["units_sold", "_u", "net_sales", "_n"])
        if ST is not None:
            def hrs(r):
                a, b = r["opening_time"], r["closing_time"]
                if pd.isna(a) or pd.isna(b):
                    return np.nan
                try:
                    x = (int(b[:2]) * 60 + int(b[3:]) - int(a[:2]) * 60 - int(a[3:])) / 60
                except Exception:
                    return np.nan
                return 24 if x == 0 else x % 24
            hh = ST.set_index("store_id").apply(hrs, axis=1)
            exp = K["store_id"].map(hh)
            emit("A", "store_daily_kpis", "open_hours_vs_horario_tienda", "Anomalía sospechosa", "open_hours difiere de closing_time-opening_time de stores", K, exp.notna() & (num(K, "open_hours") != exp) & (num(K, "net_sales") > 0), K2, ["open_hours"])

    # ================= WEATHER =================
    if W is not None:
        W2 = ["site_id", "date"]
        rel("weather_daily", "site_id", "sites", "site_id", W, pd.Series(list(sites)))
        tx_, tn, pc = num(W, "temp_max_c"), num(W, "temp_min_c"), num(W, "precip_mm")
        emit("AR", "weather_daily", "precip_faltante", "Dato faltante", "precip_mm vacío; no se imputa (no es 0 mm)", W, W["precip_mm"].isna(), W2, ["temp_max_c", "temp_min_c"])
        emit("AR", "weather_daily", "tmax_menor_que_tmin", "Error confirmado", "temp_max_c < temp_min_c; no se sabe cuál es incorrecta", W, tx_ < tn, W2, ["temp_max_c", "temp_min_c"], action="Verificar con fuente meteorológica")
        emit("AR", "weather_daily", "temperatura_fuera_de_rango", "Anomalía sospechosa", "Temperatura < -50 °C o > 55 °C", W, (tx_ > 55) | (tn < -50) | (tx_ < -50) | (tn > 55), W2, ["temp_max_c", "temp_min_c"])
        emit("AR", "weather_daily", "precipitacion_fuera_de_rango", "Anomalía sospechosa", "precip_mm < 0 o > 500", W, (pc < 0) | (pc > 500), W2, ["precip_mm"])
        if cal_min is not None:
            exp = pd.MultiIndex.from_product([sorted(sites or set(W["site_id"])), pd.date_range(cal_min, cal_max).strftime("%Y-%m-%d")])
            have = pd.MultiIndex.from_frame(W[W2].dropna())
            mis = exp.difference(have)
            if len(mis):
                MISSP.append(dict(file="weather_daily", column="(site_id,date)", missing_raw=len(mis), placeholder_tokens_raw=0, missing_clean=len(mis), pct_missing_clean=0, interpretation="Combinaciones sitio-fecha ausentes"))

    # ================= CRUCES DE FECHAS CON CALENDAR =================
    if CAL is not None:
        cs = set(CAL["date"])
        for n, c in (("store_daily_kpis", "date"), ("weather_daily", "date"), ("inventory_daily", "date")):
            if n in C:
                m = C[n][c].notna() & ~C[n][c].isin(cs)
                RELP.append(dict(source_file=n, column=c, target_file="calendar", target_column="date", rows_checked=int(C[n][c].notna().sum()), invalid_rows=int(m.sum()),
                                 distinct_invalid_values=int(C[n].loc[m, c].nunique()), sample_invalid_values=", ".join(C[n].loc[m, c].drop_duplicates().head(10)), status="REFERENCIAS INVÁLIDAS" if m.any() else "OK"))
    if "inventory_daily" in C and P is not None:
        trk = set(P.loc[P["inventory_tracked"] == "1", "sku"])
        miss = trk - set(C["inventory_daily"]["sku"])
        RELP.append(dict(source_file="products", column="sku (inventory_tracked=1)", target_file="inventory_daily", target_column="sku", rows_checked=len(trk), invalid_rows=len(miss), distinct_invalid_values=len(miss), sample_invalid_values=", ".join(sorted(miss)[:10]), status="SKUs SIN INVENTARIO" if miss else "OK"))

    # ================= ESCRITURA =================
    for n, d in C.items():
        d.drop(columns=[c for c in d.columns if c.startswith("_")]).to_csv(CLEAN / f"{n}.csv", index=False, na_rep="", lineterminator="\n")
    cat = lambda l, cols=None: pd.concat(l, ignore_index=True) if l else pd.DataFrame(columns=cols or [])
    cat(LOGP, ["file", "key", "source_line", "column", "old_value", "new_value", "reason"]).to_csv(REP / "cleaning_log.csv", index=False)
    dup = cat(DUPP, ["file", "dup_type", "key", "source_line", "kept_line", "differing_columns", "action"])
    dup.to_csv(REP / "duplicate_records.csv", index=False)
    pd.DataFrame(SKUP).to_csv(REP / "product_sku_conflicts.csv", index=False)
    pd.DataFrame(MISSP).to_csv(REP / "missing_values_report.csv", index=False)
    pd.DataFrame(RELP).to_csv(REP / "relational_integrity_report.csv", index=False)
    an, rv = cat(ANOMP), cat(REVP)
    an.to_csv(REP / "anomalies_report.csv", index=False)
    rv.to_csv(REP / "manual_review.csv", index=False)
    rules = pd.DataFrame(RULES)
    rows = []
    for n in R:
        ed = int(len(R[n]) - len(C[n]))
        kd = int(C[n].duplicated(KEYS[n], keep=False).sum())
        r = rules[rules.file == n] if len(rules) else rules
        rows.append(dict(file=n, rows_raw=len(R[n]), rows_cleaned=len(C[n]), columns=len(R[n].columns) - 1, exact_duplicate_rows_removed=ed,
                         rows_with_duplicate_business_key_after_cleaning=kd, missing_cells_clean=int(C[n].drop(columns=[c for c in C[n].columns if c.startswith("_")]).isna().sum().sum()),
                         rules_triggered=int(len(r)), total_flagged_cases=int(r["cases"].sum()) if len(r) else 0,
                         business_key=" + ".join(KEYS[n])))
    pd.DataFrame(rows).to_csv(REP / "data_quality_summary.csv", index=False)
    # ---------- validación posterior ----------
    L = ["VALIDACIÓN POSTERIOR A LA LIMPIEZA", "=" * 40]
    ok_all = True
    for n in names:
        if sha(RAW / f"{n}.csv") != hashes[n]:
            L.append(f"[FALLO] {n}.csv original fue modificado"); ok_all = False
    L.append(f"[OK] Los {len(names)} CSV originales no cambiaron (SHA-256 igual antes y después)." if ok_all else "")
    for n in R:
        cl = pd.read_csv(CLEAN / f"{n}.csv", dtype=str, keep_default_na=False, na_values=[""])
        rw = [c for c in R[n].columns if c != "_ln"]
        e1 = list(cl.columns) == rw
        e2 = int(cl.duplicated().sum())
        e3 = len(R[n]) - len(cl) == int(len(R[n]) - len(C[n]))
        L.append(f"{'[OK]' if e1 and e2 == 0 and e3 else '[REVISAR]'} {n}: raw={len(R[n])} limpio={len(cl)} retiradas={len(R[n]) - len(cl)} | columnas idénticas={e1} | duplicados exactos restantes={e2}")
    if SA is not None:
        raw_s = R["sales_daily"]; rem = raw_s[~raw_s["_ln"].isin(C["sales_daily"]["_ln"])]
        a, b, c = (pd.to_numeric(x["net_sales"], errors="coerce").sum() for x in (raw_s, rem, C["sales_daily"]))
        L.append(f"[{'OK' if abs(a - b - c) < 0.01 else 'REVISAR'}] Conciliación net_sales: raw={a:.2f} - retirado={b:.2f} = limpio={c:.2f}")
    if INV is not None:
        raw_i = R["inventory_daily"]; rem = raw_i[~raw_i["_ln"].isin(C["inventory_daily"]["_ln"])]
        a, b, c = (pd.to_numeric(x["units_sold"], errors="coerce").sum() for x in (raw_i, rem, C["inventory_daily"]))
        L.append(f"[{'OK' if a - b == c else 'REVISAR'}] Conciliación units_sold inventario: raw={a:.0f} - retirado={b:.0f} = limpio={c:.0f}")
    for n in R:
        kd = int(C[n].duplicated(KEYS[n], keep=False).sum())
        L.append(f"PENDIENTE {n}: {kd} filas comparten clave de negocio {KEYS[n]} con valores distintos (se conservan)" if kd else f"[OK] {n}: clave de negocio única")
    for r in RELP:
        if r["invalid_rows"]:
            L.append(f"PENDIENTE integridad: {r['source_file']}.{r['column']} -> {r['target_file']}: {r['invalid_rows']} filas inválidas")
    L += ["", "REGLAS DISPARADAS (casos totales, tras limpieza):"]
    if len(rules):
        for (f, ru, ca), cs_ in rules.groupby(["file", "rule", "category"])["cases"].sum().items():
            L.append(f"  - {f}: {ru} [{ca}] = {cs_}")
    L += ["", "LIMITACIONES",
          "- Los reportes anomalies/manual_review muestran como máximo %d filas por regla; el detalle completo está en data/reports/detail/." % CAP,
          "- No se imputó ningún valor faltante ni se corrigió ninguna cifra: solo espacios, moneda y marcadores '#N/A', y duplicados exactos.",
          "- Sin información externa no se puede saber qué fila es correcta en conflictos de SKU, fechas invertidas o ecuaciones de inventario rotas.",
          "- No se validó el precio de venta contra prices.csv (cobertura parcial de tienda/SKU)."]
    (REP / "validation_summary.txt").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:40]))
    print("\nListo. Salidas en", CLEAN, "y", REP)


if __name__ == "__main__":
    main()
