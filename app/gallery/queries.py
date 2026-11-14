"""Hand-written SQL for the gallery."""

# Manually pinned photos (sort_order set) come first, then by date taken.
PHOTO_ORDER = "p.sort_order IS NULL, p.sort_order, p.taken_at IS NULL, p.taken_at, p.id"


def list_albums(conn, limit=None):
    rows = conn.execute(
        """SELECT a.id, a.slug, a.title, a.description, a.event_date,
                  c.base_key AS cover_key, c.variants AS cover_variants,
                  c.placeholder AS cover_placeholder, c.width AS cover_w, c.height AS cover_h,
                  c.original_ext AS cover_ext,
                  (SELECT COUNT(*) FROM photos p WHERE p.album_id = a.id) AS photo_count
           FROM albums a LEFT JOIN photos c ON c.id = a.cover_photo_id
           ORDER BY a.event_date IS NULL, a.event_date DESC, a.created_at DESC, a.id DESC
           LIMIT ?""",
        (limit if limit else -1,),  # -1 = no limit in SQLite
    ).fetchall()
    albums = []
    for r in rows:
        d = dict(r)
        d["cover"] = (
            {"base_key": r["cover_key"], "variants": r["cover_variants"],
             "placeholder": r["cover_placeholder"], "width": r["cover_w"],
             "height": r["cover_h"], "original_ext": r["cover_ext"]}
            if r["cover_key"] else None
        )
        albums.append(d)
    return albums


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
