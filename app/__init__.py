import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask

from . import db
from .migrate import migrate

ROOT = Path(__file__).resolve().parent.parent


def create_app(test_config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        DATABASE=os.environ.get("DATABASE", str(ROOT / "data" / "app.db")),
        MIGRATIONS_DIR=str(ROOT / "migrations"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(db.close_db)

    @app.cli.command("migrate")
    def migrate_cmd():
        """Apply pending SQL migrations."""
        conn = db.connect(app.config["DATABASE"])
        applied = migrate(conn, app.config["MIGRATIONS_DIR"])
        conn.close()
        click.echo(f"Applied: {applied}" if applied else "Up to date.")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
