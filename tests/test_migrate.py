import sqlite3

from app import db
from app.migrate import migrate


def test_applies_and_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    assert migrate(conn, "migrations") == [1, 2, 3, 4, 5]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert migrate(conn, "migrations") == []


def test_failed_migration_rolls_back(tmp_path):
    mdir = tmp_path / "mig"
    mdir.mkdir()
    (mdir / "001_ok.sql").write_text("CREATE TABLE a (id INTEGER);")
    (mdir / "002_bad.sql").write_text("CREATE TABLE b (id INTEGER); BAD SQL;")
    conn = db.connect(str(tmp_path / "m.db"))
    try:
        migrate(conn, mdir)
    except sqlite3.Error:
        pass
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" not in tables


def test_foreign_keys_enforced(app):
    conn = db.connect(app.config["DATABASE"])
    try:
        conn.execute(
            "INSERT INTO photos (album_id, base_key, width, height) VALUES (999, 'k', 1, 1)"
        )
        assert False, "expected IntegrityError"
    except sqlite3.IntegrityError:
        pass
