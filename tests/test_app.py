def test_healthz(client):
    assert client.get("/healthz").json == {"status": "ok"}


def test_production_refuses_default_secret_key(monkeypatch):
    import pytest

    from app import create_app

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"SECRET_KEY": "dev", "DATABASE": "/tmp/none.db"})
    assert create_app({"SECRET_KEY": "a-real-secret", "DATABASE": "/tmp/none.db"})


def test_connect_pragmas(tmp_path):
    from app import db

    conn = db.connect(str(tmp_path / "p.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
