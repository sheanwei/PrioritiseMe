import sqlite3

import pytest

from taskbot.core import db


def test_migrate_is_idempotent():
    # conftest already migrated once
    assert db.migrate() == []
    with db.connect() as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        assert "0001_core" in versions
        assert {"items", "item_events"} <= {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_migrations_apply_in_order_and_once(tmp_path):
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "0002_b.sql").write_text("ALTER TABLE t ADD COLUMN b TEXT;")
    (mig / "0001_a.sql").write_text("CREATE TABLE t (a TEXT);")
    path = tmp_path / "m.db"
    assert db.migrate(path, mig) == ["0001_a", "0002_b"]
    assert db.migrate(path, mig) == []
    with db.connect(path) as conn:
        assert db.table_columns(conn, "t") == {"a", "b"}


def test_failed_migration_is_rolled_back_and_not_recorded(tmp_path):
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "0001_bad.sql").write_text("CREATE TABLE ok_table (a TEXT);\nTHIS IS NOT SQL;")
    path = tmp_path / "m.db"
    with pytest.raises(sqlite3.Error):
        db.migrate(path, mig)
    with db.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "ok_table" not in tables


def test_bad_migration_filename_rejected(tmp_path):
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "add-stuff.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="bad migration filename"):
        db.migrate(tmp_path / "m.db", mig)
