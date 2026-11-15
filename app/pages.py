"""Site pages: just the home page for now."""
from datetime import date

from flask import Blueprint, render_template

from .db import get_db
from .gallery import queries
from .gallery.routes import page_of

bp = Blueprint("pages", __name__)

HOME_ALBUM = "home"  # slug of the album whose photos fill the front page


@bp.app_context_processor
def site_context():
    return {"current_year": date.today().year}


@bp.get("/")
def home():
    album = queries.get_album(get_db(), HOME_ALBUM)
    photos, next_offset = page_of(album, 0) if album else ([], None)
    return render_template("home.html", album=album, photos=photos, next_offset=next_offset)

