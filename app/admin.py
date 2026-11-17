"""Temporary single-password admin: reorder albums, choose and frame covers, delete photos.

Deliberately small so it can be swapped for real logins later (see the login-wip branch): the only
auth check is `g.admin`. Everything here answers 404 to anyone who is not signed in.
"""
import logging
import secrets
import time
from collections import defaultdict, deque

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from .db import get_db
from .gallery import queries
from .storage import get_storage

bp = Blueprint("admin", __name__)
log = logging.getLogger(__name__)

FAIL_LIMIT, FAIL_WINDOW = 6, 600  # wrong passwords allowed per address per 10 minutes (per worker)
MAX_DELETE = 500


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


@bp.app_context_processor
def admin_context():
    return {"csrf_token": csrf_token}


@bp.before_app_request
def load_admin():
    g.admin = bool(current_app.config.get("ADMIN_PASSWORD_HASH")) and session.get("admin") is True


@bp.before_request
def guard():
    if request.endpoint != "admin.login" and not g.admin:
        abort(404)  # don't reveal that these routes exist
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
        expected = session.get("csrf") or ""
        if not expected or not secrets.compare_digest(sent, expected):  # an empty token never matches
            abort(400)


def too_many_failures(ip):
    hits = current_app.extensions.setdefault("admin_fails", defaultdict(deque))[ip]
    while hits and time.monotonic() - hits[0] > FAIL_WINDOW:
        hits.popleft()
    return len(hits) >= FAIL_LIMIT


def note_failure(ip):
    current_app.extensions.setdefault("admin_fails", defaultdict(deque))[ip].append(time.monotonic())


@bp.route("/admin", methods=["GET", "POST"])
def login():
    hashed = current_app.config.get("ADMIN_PASSWORD_HASH")
    if not hashed:
        abort(404)
    if g.admin:
        return redirect(url_for("gallery.albums"))
    error, status = None, 200
    if request.method == "POST":
        if too_many_failures(request.remote_addr):
            error, status = "Too many attempts. Try again in a few minutes.", 429
        elif check_password_hash(hashed, request.form.get("password", "")):
            session.clear()  # fresh session on sign-in
            session["admin"] = True
            session.permanent = True
            return redirect(url_for("gallery.albums"))
        else:
            note_failure(request.remote_addr)
            error, status = "Wrong password.", 401
    return render_template("admin/login.html", error=error), status


@bp.post("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("pages.home"))


def payload():
    return request.get_json(silent=True) or request.form


@bp.post("/admin/albums/order")
def order_albums():
    slugs = (request.get_json(silent=True) or {}).get("slugs")
    if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
        abort(400)
    conn = get_db()
    for position, slug in enumerate(slugs, 1):
        conn.execute("UPDATE albums SET sort_order = ? WHERE slug = ?", (position, slug))
    conn.commit()
    return jsonify(ok=True)


def _album(slug):
    return get_db().execute("SELECT id FROM albums WHERE slug = ?", (slug,)).fetchone() or abort(404)


def _ints(value):
    if not isinstance(value, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in value):
        abort(400)
    return value


@bp.post("/admin/albums/<slug>/highlights")
def edit_highlights(slug):
    """Add photos to, or remove them from, an album's highlights (the card cycles through them)."""
    album = _album(slug)
    data = request.get_json(silent=True) or {}
    add, remove = _ints(data.get("add", [])), _ints(data.get("remove", []))
    conn = get_db()
    if add:
        marks = ",".join("?" * len(add))
        found = {r[0] for r in conn.execute(
            f"SELECT id FROM photos WHERE album_id = ? AND id IN ({marks})", [album["id"], *add])}
        if found != set(add):
            abort(400)
    if remove:
        conn.execute(f"DELETE FROM album_highlights WHERE album_id = ? AND photo_id IN ({','.join('?' * len(remove))})",
                     [album["id"], *remove])
    have = {r[0] for r in conn.execute("SELECT photo_id FROM album_highlights WHERE album_id = ?", (album["id"],))}
    new = [pid for pid in dict.fromkeys(add) if pid not in have]  # keeps the order given, drops repeats
    if len(have) + len(new) > queries.MAX_HIGHLIGHTS:
        conn.rollback()
        return jsonify(error=f"An album can have at most {queries.MAX_HIGHLIGHTS} highlights."), 400
    position = conn.execute("SELECT COALESCE(MAX(position), 0) FROM album_highlights WHERE album_id = ?",
                            (album["id"],)).fetchone()[0]
    for pid in new:
        position += 1
        conn.execute("INSERT INTO album_highlights (album_id, photo_id, position) VALUES (?, ?, ?)",
                     (album["id"], pid, position))
    conn.commit()
    ids = [r[0] for r in conn.execute(
        "SELECT photo_id FROM album_highlights WHERE album_id = ? ORDER BY position", (album["id"],))]
    return jsonify(ok=True, highlights=ids)


def _percent(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        abort(400)
    if not 0 <= number <= 100:
        abort(400)
    return number


@bp.post("/admin/albums/<slug>/crop")
def set_crop(slug):
    """Save how each highlight is framed in the card, and whether the card shows photos whole."""
    album = _album(slug)
    data = payload()
    fit = data.get("fit")
    if fit not in ("crop", "full"):
        abort(400)
    conn = get_db()
    items = data.get("items")
    if items is not None:
        if not isinstance(items, list):
            abort(400)
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("photo_id"), int):
                abort(400)
            conn.execute("UPDATE album_highlights SET x = ?, y = ? WHERE album_id = ? AND photo_id = ?",
                         (_percent(item.get("x")), _percent(item.get("y")), album["id"], item["photo_id"]))
    else:  # no highlights chosen: the single fallback cover is framed on the album itself
        conn.execute("UPDATE albums SET cover_x = ?, cover_y = ? WHERE id = ?",
                     (_percent(data.get("x")), _percent(data.get("y")), album["id"]))
    conn.execute("UPDATE albums SET cover_fit = ? WHERE id = ?", (fit, album["id"]))
    conn.commit()
    return jsonify(ok=True)


@bp.get("/admin/panel")
def panel():
    conn = get_db()
    albums = queries.list_albums(conn)
    return render_template("admin/panel.html", settings=queries.get_settings(conn),
                           preview=next((a for a in albums if len(a["slides"]) > 1), None))


@bp.post("/admin/settings")
def save_settings():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    try:
        seconds = float(data.get("seconds"))
    except (TypeError, ValueError):
        abort(400)
    if mode not in queries.HIGHLIGHT_MODES or not 1.5 <= seconds <= 30:
        abort(400)
    conn = get_db()
    for key, value in (("highlight_mode", mode), ("highlight_seconds", f"{seconds:g}")):
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()
    return jsonify(ok=True)


@bp.post("/admin/photos/delete")
def delete_photos():
    ids = (request.get_json(silent=True) or {}).get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_DELETE or not all(isinstance(i, int) for i in ids):
        abort(400)
    conn = get_db()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, album_id, base_key, variants, original_ext, source_sha256 FROM photos WHERE id IN ({marks})",
        ids).fetchall()
    keys = []
    for r in rows:
        if r["source_sha256"]:  # remember it, so re-running an import doesn't bring the photo back
            conn.execute("INSERT OR IGNORE INTO removed_photos (album_id, source_sha256) VALUES (?, ?)",
                         (r["album_id"], r["source_sha256"]))
        keys += [f"{r['base_key']}/{w}.webp" for w in r["variants"].split(",")]
        keys.append(f"{r['base_key']}/original.{r['original_ext']}")
    conn.execute(f"DELETE FROM photos WHERE id IN ({marks})", [r["id"] for r in rows])
    conn.commit()  # the database is the source of truth: do this first so the site never shows broken images
    leftover = False
    try:
        get_storage().delete_keys(keys)
    except Exception:
        log.exception("Deleted %s photo rows but could not delete their files", len(rows))
        leftover = True
    return jsonify(deleted=len(rows), files_left_behind=leftover)
