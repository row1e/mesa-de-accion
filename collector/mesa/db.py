"""Almacenamiento SQLite (WAL). Esquema pensado para migrar a Postgres/PostGIS sin cambios de modelo.

Tablas:
  fetches        — cada petición HTTP: tiempos, estado, bytes, hash y ruta del crudo guardado
  runs           — cada ejecución de una fuente (programada o manual) con su resultado
  source_health  — estado actual por fuente (último éxito, fallos consecutivos, próximo turno)
  items          — registros normalizados por (source, kind, key) con first_seen/last_seen/current
"""
import contextlib
import json
import sqlite3
import threading
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL, started_at REAL NOT NULL,
  duration_ms INTEGER, http_status INTEGER, bytes INTEGER, sha256 TEXT, changed INTEGER, path TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS fetches_src ON fetches(source, started_at);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, trigger TEXT NOT NULL, started_at REAL NOT NULL,
  finished_at REAL, ok INTEGER, items INTEGER, new_items INTEGER, message TEXT);
CREATE INDEX IF NOT EXISTS runs_src ON runs(source, started_at);
CREATE TABLE IF NOT EXISTS source_health(
  source TEXT PRIMARY KEY, interval_s INTEGER, last_run REAL, last_ok REAL, last_error REAL,
  last_message TEXT, consecutive_failures INTEGER DEFAULT 0, items INTEGER, running INTEGER DEFAULT 0,
  last_manual REAL);
CREATE TABLE IF NOT EXISTS items(
  source TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  current INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL, PRIMARY KEY(source, kind, key));
CREATE INDEX IF NOT EXISTS items_kind ON items(source, kind, current);
CREATE TABLE IF NOT EXISTS details(
  source TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, fetched_at REAL NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(source, kind, key));
"""

_local = threading.local()


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        config.DATA.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return c


def init():
    conn().executescript(SCHEMA)
    from .sources import gobpe          # migración única: fuente combinada "comunicados" → una por institución
    gobpe.migrate_combined()


@contextlib.contextmanager
def tx():
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def upsert_items(source, kind, records, snapshot=False):
    """records: dict key -> payload. snapshot=True marca como no vigentes las claves ausentes.

    Devuelve (total, nuevos)."""
    now = time.time()
    new = 0
    with tx() as c:
        existing = {r["key"] for r in c.execute("SELECT key FROM items WHERE source=? AND kind=?", (source, kind))}
        for key, payload in records.items():
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if key in existing:
                c.execute("UPDATE items SET last_seen=?, current=1, payload=? WHERE source=? AND kind=? AND key=?",
                          (now, data, source, kind, key))
            else:
                new += 1
                c.execute("INSERT INTO items(source,kind,key,first_seen,last_seen,current,payload) VALUES(?,?,?,?,?,1,?)",
                          (source, kind, key, now, now, data))
        if snapshot:
            gone = existing - set(records)
            c.executemany("UPDATE items SET current=0 WHERE source=? AND kind=? AND key=?",
                          [(source, kind, k) for k in gone])
    return len(records), new


def get_items(source, kind, current_only=True, limit=None, order="first_seen DESC"):
    q = f"SELECT key, first_seen, last_seen, current, payload FROM items WHERE source=? AND kind=?"
    if current_only:
        q += " AND current=1"
    q += f" ORDER BY {order}"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [{**json.loads(r["payload"]), "_key": r["key"], "_first_seen": r["first_seen"], "_last_seen": r["last_seen"],
             "_current": r["current"]} for r in conn().execute(q, (source, kind))]


def get_item(source, kind, key):
    r = conn().execute("SELECT key, first_seen, last_seen, current, payload FROM items WHERE source=? AND kind=? AND key=?",
                       (source, kind, key)).fetchone()
    return None if r is None else {**json.loads(r["payload"]), "_key": r["key"], "_first_seen": r["first_seen"],
                                   "_last_seen": r["last_seen"], "_current": r["current"]}


def detail_get(source, kind, key, max_age):
    r = conn().execute("SELECT fetched_at, data FROM details WHERE source=? AND kind=? AND key=?", (source, kind, key)).fetchone()
    if r and time.time() - r["fetched_at"] < max_age:
        return json.loads(r["data"]), r["fetched_at"]
    return None, None


def detail_set(source, kind, key, data):
    conn().execute("INSERT OR REPLACE INTO details(source,kind,key,fetched_at,data) VALUES(?,?,?,?,?)",
                   (source, kind, key, time.time(), json.dumps(data, ensure_ascii=False)))


def has_item(source, kind, key):
    return conn().execute("SELECT 1 FROM items WHERE source=? AND kind=? AND key=?", (source, kind, key)).fetchone() is not None


def health_all():
    return [dict(r) for r in conn().execute("SELECT * FROM source_health ORDER BY source")]


def health_set(source, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    c = conn()
    c.execute("INSERT OR IGNORE INTO source_health(source) VALUES(?)", (source,))
    c.execute(f"UPDATE source_health SET {cols} WHERE source=?", (*fields.values(), source))
