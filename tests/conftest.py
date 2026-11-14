import pytest

from app import create_app, db
from app.migrate import migrate


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})
    conn = db.connect(app.config["DATABASE"])
    migrate(conn, app.config["MIGRATIONS_DIR"])
    conn.close()
    return app


@pytest.fixture
def client(app):
    return app.test_client()
