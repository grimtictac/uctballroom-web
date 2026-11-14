"""Static-ish site pages: home, about, join, events."""
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
    albums = queries.list_albums(get_db(), limit=3)
    return render_template("home.html", albums=albums)


@bp.get("/about")
def about():
    return render_template("about.html")


@bp.get("/join")
def join():
    return render_template("join.html")


@bp.get("/events")
def events():
    return render_template("events.html")
