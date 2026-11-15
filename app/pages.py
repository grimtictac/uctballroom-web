"""Site pages: just the home page for now."""
from datetime import date

from flask import Blueprint, render_template

from .db import get_db
from .gallery import queries

bp = Blueprint("pages", __name__)


@bp.app_context_processor
def site_context():
    return {"current_year": date.today().year}


@bp.get("/")
def home():
    albums = queries.list_albums(get_db())
    return render_template("home.html", albums=albums)

