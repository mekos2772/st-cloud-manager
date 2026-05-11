import sqlite3
from contextlib import contextmanager
from manager.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _migrate(conn: sqlite3.Connection):
    """Add new columns to existing tables without dropping data."""
    # system_settings table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    # instances table — new columns added safely
    new_columns = [
        ("api_status", "TEXT DEFAULT 'unchecked'"),
        ("api_error", "TEXT"),
        ("api_checked_at", "TEXT"),
        ("stream_status", "TEXT DEFAULT 'unchecked'"),
        ("stream_error", "TEXT"),
        ("stream_checked_at", "TEXT"),
        ("web_status", "TEXT DEFAULT 'unchecked'"),
        ("web_error", "TEXT"),
        ("web_checked_at", "TEXT"),
        ("ready", "INTEGER DEFAULT 0"),
        ("cf_record_id", "TEXT"),
        ("custom_domain", "TEXT"),
        ("path_prefix", "TEXT"),
        ("is_trial", "INTEGER DEFAULT 0"),
        ("last_activity", "TEXT"),
        ("client_ip", "TEXT"),
        ("proxy_key_alias", "TEXT"),
    ]
    for col_name, col_def in new_columns:
        if not _column_exists(conn, "instances", col_name):
            conn.execute(f"ALTER TABLE instances ADD COLUMN {col_name} {col_def}")


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS activation_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'unused',
                plan TEXT DEFAULT 'default',
                days INTEGER DEFAULT 30,
                created_at TEXT NOT NULL,
                used_at TEXT,
                instance_id TEXT
            );

            CREATE TABLE IF NOT EXISTS instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT UNIQUE NOT NULL,
                domain TEXT UNIQUE NOT NULL,
                container_name TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                api_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trial_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_ip TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting',
                instance_id TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                error TEXT
            );
        """)
        _migrate(conn)
