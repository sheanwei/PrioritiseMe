"""SQLite connection helper and migrations runner.

Migrations are ``migrations/NNNN_name.sql`` files applied in filename order,
each exactly once, each inside its own transaction. Slices own number ranges:
S0=0000s, S1=0100s, S2=0200s, S3=0300s, S4=0400s, S5=0500s, S6=0600s.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import clock
from .config import ROOT, settings

MIGRATIONS_DIR = ROOT / "migrations"
_VERSION_RE = re.compile(r"^\d{4}_[a-z0-9_]+$")


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, always close."""
    db_path = Path(path) if path is not None else settings().db_path
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(path: Path | str | None = None, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied by this call."""
    db_path = Path(path) if path is not None else settings().db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    newly_applied: list[str] = []
    for file in sorted(migrations_dir.glob("*.sql")):
        version = file.stem
        if not _VERSION_RE.match(version):
            raise ValueError(f"bad migration filename {file.name!r}; expected NNNN_name.sql")
        if version in applied:
            continue
        sql = file.read_text(encoding="utf-8")
        script = (
            "BEGIN;\n"
            f"{sql}\n;\n"
            "INSERT INTO schema_migrations (version, applied_at) "
            f"VALUES ('{version}', '{clock.now_iso()}');\n"
            "COMMIT;"
        )
        with connect(db_path) as conn:
            conn.executescript(script)
        newly_applied.append(version)
    return newly_applied


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}
