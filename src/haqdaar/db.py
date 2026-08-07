"""SQLite connection helper. Schema and loading are owned by scripts/ingest.py
(Step 1) - ingest.py's inline SCHEMA is authoritative, not attribute_schema.sql
/ scheme_catalogue.sql (superseded, see CHANGE_REPORT.md #7)."""
import sqlite3

from haqdaar import config


def get_db(path: str | None = None) -> sqlite3.Connection:
    """Open the scheme DB. Caller answers never go through this connection -
    they live only in engine state (PRD M13)."""
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn
