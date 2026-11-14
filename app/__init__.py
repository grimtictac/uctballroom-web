import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask

from . import db
from .importer import import_album_cmd
from .migrate import migrate

ROOT = Path(__file__).resolve().parent.parent


def create_app(test_config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        DATABASE=os.environ.get("DATABASE", str(ROOT / "data" / "app.db")),
        MIGRATIONS_DIR=str(ROOT / "migrations"),
        SPACES_KEY=os.environ.get("SPACES_KEY"),
        SPACES_SECRET=os.environ.get("SPACES_SECRET"),
        SPACES_BUCKET=os.environ.get("SPACES_BUCKET"),
        SPACES_REGION=os.environ.get("SPACES_REGION"),
        SPACES_PUBLIC_BASE_URL=os.environ.get("SPACES_PUBLIC_BASE_URL"),
        SPACES_PREFIX=os.environ.get("SPACES_PREFIX", ""),
    )
    if test_config:
        app.config.update(test_config)
    if os.environ.get("APP_ENV") == "production" and app.config["SECRET_KEY"] in (None, "", "dev"):
        raise RuntimeError("SECRET_KEY must be set to a real secret when APP_ENV=production")
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(db.close_db)
    app.cli.add_command(import_album_cmd)

    @app.cli.command("migrate")
    def migrate_cmd():
        """Apply pending SQL migrations."""
        conn = db.connect(app.config["DATABASE"])
        applied = migrate(conn, app.config["MIGRATIONS_DIR"])
        conn.close()
        click.echo(f"Applied: {applied}" if applied else "Up to date.")

    from .gallery.routes import bp as gallery_bp
    from .pages import bp as pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(gallery_bp)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
