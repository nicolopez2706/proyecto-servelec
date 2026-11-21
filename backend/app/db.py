import sqlite3
from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_fail (
  email TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_fail ON login_fail(email, at);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  params TEXT NOT NULL,
  sim TEXT,
  result TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  by TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  by TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS catalog (
  code TEXT PRIMARY KEY,
  desc TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  alts TEXT NOT NULL DEFAULT '[]',
  created_by TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  what TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'abierta',
  by TEXT NOT NULL,
  at TEXT NOT NULL,
  code TEXT,
  resolved_by TEXT,
  resolved_at TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS conv (
  key TEXT PRIMARY KEY,
  factor REAL NOT NULL,
  note TEXT,
  by TEXT,
  at TEXT
);
"""


def connect():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.db_path, timeout=15)      # si otra operación está escribiendo, espera en vez de fallar
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    con = connect()
    con.executescript(SCHEMA)
    # Bases creadas antes de esta version: agregar lo que falta (sin tocar los datos existentes).
    cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if "must_change" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
    con.commit()
    con.close()
