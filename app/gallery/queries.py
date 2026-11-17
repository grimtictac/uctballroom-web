"""Hand-written SQL for the gallery."""

import random
from collections import defaultdict

HOME_ALBUM = "home"  # slug of the album shown on the front page; it is not listed with the others

# Manually pinned photos (sort_order set) come first, then by date taken.
PHOTO_ORDER = "p.sort_order IS NULL, p.sort_order, p.taken_at IS NULL, p.taken_at, p.id"


MAX_HIGHLIGHTS = 200  # a safety limit; slides load on demand, so a big album can have one per couple
DEFAULT_SETTINGS = {"highlight_mode": "fade", "highlight_seconds": "4", "highlight_shuffle": "1"}
HIGHLIGHT_MODES = ("fade", "slide", "off")


def get_settings(conn):
    """The admin's display settings, with defaults for anything not set yet."""
    values = {**DEFAULT_SETTINGS, **{r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}}
    return {"mode": values["highlight_mode"], "seconds": float(values["highlight_seconds"]),
            "shuffle": values["highlight_shuffle"] == "1"}


def shuffled(albums, settings):
    """With shuffle on, each visit sees each card's highlights in a fresh random order, so every
    highlight gets a turn at being the first one shown (and none languishes at the end)."""
    if settings["shuffle"]:
        for a in albums:
            random.shuffle(a["slides"])
    return albums


COVER_PHOTO = f"""COALESCE(a.cover_photo_id,
    (SELECT p.id FROM photos p WHERE p.album_id = a.id ORDER BY {PHOTO_ORDER} LIMIT 1))"""


def list_albums(conn, exclude=HOME_ALBUM):
    """All albums except `exclude`: manually ordered ones first, then the rest alphabetically."""
    rows = conn.execute(
        f"""SELECT a.id, a.slug, a.title, a.description, a.event_date, a.cover_x, a.cover_y, a.cover_fit,
                  c.id AS cover_id, c.base_key AS cover_key, c.variants AS cover_variants,
                  c.placeholder AS cover_placeholder, c.width AS cover_w, c.height AS cover_h,
                  c.original_ext AS cover_ext,
                  (SELECT COUNT(*) FROM photos p WHERE p.album_id = a.id) AS photo_count
           FROM albums a LEFT JOIN photos c ON c.id = {COVER_PHOTO}
           WHERE a.slug != ?
           ORDER BY a.sort_order IS NULL, a.sort_order, a.title COLLATE NOCASE, a.id""",
        (exclude or "",),
    ).fetchall()
    albums = []
    for r in rows:
        d = dict(r)
        d["cover"] = (
            {"id": r["cover_id"], "base_key": r["cover_key"], "variants": r["cover_variants"],
             "placeholder": r["cover_placeholder"], "width": r["cover_w"],
             "height": r["cover_h"], "original_ext": r["cover_ext"]}
            if r["cover_key"] else None
        )
        albums.append(d)
    # Each card cycles through the album's highlight photos; with none chosen it shows the one cover.
    slides = defaultdict(list)
    for r in conn.execute(
            """SELECT h.album_id, h.x, h.y, p.id, p.base_key, p.variants, p.placeholder, p.width, p.height,
                      p.original_ext
               FROM album_highlights h JOIN photos p ON p.id = h.photo_id ORDER BY h.album_id, h.position"""):
        slides[r["album_id"]].append(dict(r))
    for d in albums:
        d["slides"] = slides.get(d["id"]) or (
            [{**d["cover"], "x": d["cover_x"], "y": d["cover_y"]}] if d["cover"] else [])
    return albums


def album_slides(conn, album):
    """(slides, is_fallback): the album's highlight photos with their framing, or its single cover."""
    rows = conn.execute(
        """SELECT p.*, h.x, h.y FROM album_highlights h JOIN photos p ON p.id = h.photo_id
           WHERE h.album_id = ? ORDER BY h.position""", (album["id"],)).fetchall()
    if rows:
        return [dict(r) for r in rows], False
    cover = album_cover(conn, album)
    return ([{**dict(cover), "x": album["cover_x"], "y": album["cover_y"]}] if cover else []), True


def album_layout(conn, album_id):
    """(ordered photo ids, {photo id: group id or None}, {group id: {id, start, people}}) for one album.

    A group starts at its marker photo and runs to the next marker; photos before the first marker are
    in no group. Computed in Python because an album has hundreds of photos at most."""
    ids = [r[0] for r in conn.execute(
        f"SELECT p.id FROM photos p WHERE p.album_id = ? ORDER BY {PHOTO_ORDER}", (album_id,))]
    groups = {}
    for r in conn.execute(
            """SELECT g.id, g.start_photo_id, t.name FROM photo_groups g
               LEFT JOIN group_people gp ON gp.group_id = g.id LEFT JOIN tags t ON t.id = gp.tag_id
               WHERE g.album_id = ? ORDER BY g.id, gp.position""", (album_id,)):
        group = groups.setdefault(r["id"], {"id": r["id"], "start": r["start_photo_id"], "people": []})
        if r["name"]:
            group["people"].append(r["name"])
    by_start = {g["start"]: gid for gid, g in groups.items()}
    group_of, current = {}, None
    for pid in ids:
        current = by_start.get(pid, current)
        group_of[pid] = current
    return ids, group_of, groups


def highlight_ids(conn, album_id):
    return {r[0] for r in conn.execute("SELECT photo_id FROM album_highlights WHERE album_id = ?", (album_id,))}


def album_cover(conn, album):
    """The photo shown on the album's card: the chosen cover, else the first photo."""
    return conn.execute(
        f"SELECT p.* FROM photos p WHERE p.id = (SELECT {COVER_PHOTO} FROM albums a WHERE a.id = ?)",
        (album["id"],),
    ).fetchone()


def get_album(conn, slug):
    return conn.execute(
        """SELECT a.*, (SELECT COUNT(*) FROM photos p WHERE p.album_id = a.id) AS photo_count
           FROM albums a WHERE slug = ?""",
        (slug,),
    ).fetchone()


def album_photos(conn, album_id, limit, offset):
    """Return (photos, has_more) for one page of an album."""
    rows = conn.execute(
        f"SELECT p.* FROM photos p WHERE p.album_id = ? ORDER BY {PHOTO_ORDER} LIMIT ? OFFSET ?",
        (album_id, limit + 1, offset),
    ).fetchall()
    return rows[:limit], len(rows) > limit


def get_photo(conn, photo_id):
    return conn.execute(
        """SELECT p.*, a.slug AS album_slug, a.title AS album_title
           FROM photos p JOIN albums a ON a.id = p.album_id WHERE p.id = ?""",
        (photo_id,),
    ).fetchone()


def neighbours(conn, album_id, photo_id):
    """Return (prev_id, next_id) of a photo within its album's order."""
    row = conn.execute(
        f"""WITH o AS (
                SELECT p.id, LAG(p.id) OVER w AS prev_id, LEAD(p.id) OVER w AS next_id
                FROM photos p WHERE p.album_id = ?
                WINDOW w AS (ORDER BY {PHOTO_ORDER})
            ) SELECT prev_id, next_id FROM o WHERE id = ?""",
        (album_id, photo_id),
    ).fetchone()
    return (row["prev_id"], row["next_id"]) if row else (None, None)
