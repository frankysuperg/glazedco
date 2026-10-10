"""Caché y presupuesto persistentes locales (SQLite); nunca guardan la API key."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .models import SocialFailure


class LocalCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, expires REAL, payload TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS usage (account TEXT, month TEXT, attempts INTEGER, PRIMARY KEY(account,month))")
            db.execute("CREATE TABLE IF NOT EXISTS throttle (account TEXT PRIMARY KEY, next_at REAL)")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def get(self, key: str, now: float) -> str | None:
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM cache WHERE expires <= ?", (now,))
            row = db.execute("SELECT payload FROM cache WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def put(self, key: str, payload: str, expires: float) -> None:
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, expires, payload))

    def reserve(self, account: str, now: float, budget: int, interval: float) -> None:
        month = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT next_at FROM throttle WHERE account=?", (account,)).fetchone()
            if row and row[0] > now:
                raise SocialFailure("LIMITE_DE_USO", "Espera antes de volver a consultar TrendsAPI.")
            row = db.execute("SELECT attempts FROM usage WHERE account=? AND month=?", (account, month)).fetchone()
            count = row[0] if row else 0
            if count >= budget:
                raise SocialFailure("PRESUPUESTO_AGOTADO", "Se alcanzó el presupuesto local mensual de llamadas.")
            db.execute("INSERT OR REPLACE INTO usage VALUES (?,?,?)", (account, month, count + 1))
            db.execute("INSERT OR REPLACE INTO throttle VALUES (?,?)", (account, now + interval))

    def defer(self, account: str, until: float) -> None:
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO throttle VALUES (?,?) ON CONFLICT(account) DO UPDATE SET next_at=MAX(next_at,excluded.next_at)",
                       (account, until))
