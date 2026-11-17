"""Site pages: just the home page for now."""
from datetime import date
from functools import cache
from pathlib import Path

from flask import Blueprint, current_app, render_template, url_for

from .db import get_db
from .gallery import queries
from .gallery.routes import page_of

bp = Blueprint("pages", __name__)



@cache
def _file_version(path):
    try:
        return int(Path(path).stat().st_mtime)
    except OSError:
        return 0


def asset(filename):
    """Static file URL stamped with the file's modification time, so a changed file is never served from
    a browser's cache (static files are cached for a day, and a stale stylesheet breaks the new HTML)."""
    version = _file_version(str(Path(current_app.static_folder) / filename))
    return url_for("static", filename=filename, v=version)


@bp.app_context_processor
def site_context():
    return {"current_year": date.today().year, "asset": asset}


@bp.get("/")
def home():
    album = queries.get_album(get_db(), queries.HOME_ALBUM)
    photos, next_offset = page_of(album, 0) if album else ([], None)
    return render_template("home.html", album=album, photos=photos, next_offset=next_offset)

