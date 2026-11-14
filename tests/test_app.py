def test_healthz(client):
    assert client.get("/healthz").json == {"status": "ok"}


def test_production_refuses_default_secret_key(monkeypatch):
    import pytest

    from app import create_app

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"SECRET_KEY": "dev", "DATABASE": "/tmp/none.db"})
    assert create_app({"SECRET_KEY": "a-real-secret", "DATABASE": "/tmp/none.db"})
