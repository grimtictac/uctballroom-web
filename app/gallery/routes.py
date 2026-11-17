from flask import Blueprint, abort, current_app, g, render_template, request

from .. import people
from ..db import get_db
from ..storage import get_storage
from . import queries

bp = Blueprint("gallery", __name__)

ROW_HEIGHT = 240  # target row height (px) of the justified grid; keep in sync with CSS


def variants_of(photo):
    return [int(w) for w in photo["variants"].split(",")]


def best_width(photo, target):
    ws = variants_of(photo)
    return next((w for w in ws if w >= target), ws[-1])


def photo_url(photo, width):
    return get_storage().public_url(f"{photo['base_key']}/{width}.webp")


def photo_srcset(photo):
    return ", ".join(f"{photo_url(photo, w)} {w}w" for w in variants_of(photo))


def original_url(photo):
    return get_storage().public_url(f"{photo['base_key']}/original.{photo['original_ext']}")


@bp.app_context_processor
def helpers():
    return {
        "photo_url": photo_url,
        "photo_srcset": photo_srcset,
        "best_width": best_width,
        "original_url": original_url,
        "row_height": ROW_HEIGHT,
    }


def highlight_data(conn, album):
    """What the admin's framing dialog needs (None for everyone else)."""
    if not g.get("admin"):
        return None
    slides, fallback = queries.album_slides(conn, album)
    return {"slug": album["slug"], "fit": album["cover_fit"], "fallback": fallback,
            "slides": [{"id": s["id"], "src": photo_url(s, best_width(s, 800)), "w": s["width"], "h": s["height"],
                        "x": s["x"], "y": s["y"]} for s in slides]}


@bp.get("/albums/")
def albums():
    conn = get_db()
    hl = queries.get_settings(conn)
    return render_template("gallery/albums.html", albums=queries.shuffled(queries.list_albums(conn), hl), hl=hl)


def page_of(album, offset):
    size = current_app.config.get("GALLERY_PAGE_SIZE", 60)
    photos, has_more = queries.album_photos(get_db(), album["id"], size, offset)
    return photos, (offset + size if has_more else None)


def group_context(layout, offset):
    """What the tile template needs to draw group headings for a page that starts at `offset`."""
    ids, group_of, groups = layout
    return {"gmap": group_of, "ginfo": groups, "prev_gid": group_of.get(ids[offset - 1]) if 0 < offset <= len(ids) else None}


def album_index(conn, album, layout):
    """The highlights as an index of the album, in album order, each captioned with its group's dancers."""
    ids, group_of, groups = layout
    slides, fallback = queries.album_slides(conn, album)
    if fallback:
        return []
    slides.sort(key=lambda sl: ids.index(sl["id"]) if sl["id"] in ids else len(ids))
    return [{"id": sl["id"], "src": photo_url(sl, best_width(sl, 400)), "x": sl["x"], "y": sl["y"],
             "w": sl["width"], "h": sl["height"], "placeholder": sl["placeholder"],
             "people": groups[group_of[sl["id"]]]["people"] if group_of.get(sl["id"]) else []}
            for sl in slides]


@bp.get("/albums/<slug>")
def album(slug):
    conn = get_db()
    album = queries.get_album(conn, slug) or abort(404)
    layout = queries.album_layout(conn, album["id"])
    ids = layout[0]
    at = request.args.get("at", type=int)
    offset = ids.index(at) if at in ids else 0  # ?at=<photo> opens the album starting at that photo
    photos, next_offset = page_of(album, offset)
    return render_template("gallery/album.html", album=album, photos=photos, next_offset=next_offset,
                           highlights=queries.highlight_ids(conn, album["id"]),
                           highlight_data=highlight_data(conn, album), jumped=offset > 0,
                           index=album_index(conn, album, layout),
                           people_names=people.all_people(conn) if g.get("admin") else [],
                           **group_context(layout, 0))


@bp.get("/albums/<slug>/photos")
def album_photos(slug):
    """htmx fragment: the next page of tiles plus an out-of-band replacement of the sentinel."""
    conn = get_db()
    album = queries.get_album(conn, slug) or abort(404)
    offset = max(0, request.args.get("offset", 0, type=int))
    layout = queries.album_layout(conn, album["id"])
    photos, next_offset = page_of(album, offset)
    return render_template("gallery/_page.html", album=album, photos=photos, next_offset=next_offset,
                           highlights=queries.highlight_ids(conn, album["id"]), **group_context(layout, offset))


@bp.get("/photo/<int:photo_id>")
def photo(photo_id):
    conn = get_db()
    photo = queries.get_photo(conn, photo_id) or abort(404)
    prev_id, next_id = queries.neighbours(conn, photo["album_id"], photo_id)
    return render_template("gallery/photo.html", photo=photo, prev_id=prev_id, next_id=next_id)
