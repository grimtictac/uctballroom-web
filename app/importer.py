"""Import a folder of photos into an album: process, upload to the Space, insert rows."""
import hashlib
import re
import secrets
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import db as dbmod
from .images import CONTENT_TYPE, UnsupportedImage, process
from .storage import get_storage

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def get_or_create_album(conn, slug, title=None, event_date=None):
    if not SLUG_RE.match(slug):
        raise ValueError("album slug must be lowercase letters, digits and hyphens")
    row = conn.execute("SELECT id FROM albums WHERE slug = ?", (slug,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO albums (slug, title, event_date) VALUES (?, ?, ?)",
        (slug, title or slug.replace("-", " ").title(), event_date),
    )
    conn.commit()
    return cur.lastrowid


def import_photo(conn, storage, album_id, album_slug, data, credit=None, sort_order=None):
    """Import one photo. Returns the new photo id, or None if it was already imported."""
    sha = hashlib.sha256(data).hexdigest()
    if conn.execute(
        "SELECT 1 FROM photos WHERE album_id = ? AND source_sha256 = ?", (album_id, sha)
    ).fetchone():
        return None

    p = process(data)
    base_key = f"albums/{album_slug}/{secrets.token_hex(6)}"
    try:
        for width, blob in p.variants.items():
            storage.upload(f"{base_key}/{width}.webp", blob, "image/webp")
        storage.upload(
            f"{base_key}/original.{p.original_ext}", p.original, CONTENT_TYPE[p.original_ext]
        )
        cur = conn.execute(
            """INSERT INTO photos (album_id, base_key, width, height, credit, taken_at,
                                   placeholder, original_ext, variants, source_sha256, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (album_id, base_key, p.width, p.height, credit, p.taken_at, p.placeholder,
             p.original_ext, ",".join(map(str, p.variants)), sha, sort_order),
        )
        conn.execute(
            "UPDATE albums SET cover_photo_id = ? WHERE id = ? AND cover_photo_id IS NULL",
            (cur.lastrowid, album_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        storage.delete_prefix(base_key)  # don't leave orphaned files behind
        raise
    return cur.lastrowid


def import_folder(conn, storage, folder, slug, title=None, credit=None, event_date=None,
                  log=lambda msg: None):
    album_id = get_or_create_album(conn, slug, title, event_date)
    summary = {"imported": 0, "skipped": 0, "failed": 0}
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in PHOTO_SUFFIXES)
    # Photos show in filename order: number them after whatever the album already holds.
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) FROM photos WHERE album_id = ?", (album_id,)
    ).fetchone()[0]
    for path in files:
        try:
            photo_id = import_photo(conn, storage, album_id, slug, path.read_bytes(), credit,
                                    sort_order=order + 1)
        except UnsupportedImage as e:
            summary["failed"] += 1
            log(f"FAILED  {path.name}: {e}")
            continue
        if photo_id is None:
            summary["skipped"] += 1
            log(f"skipped {path.name} (already imported)")
        else:
            summary["imported"] += 1
            order += 1
            log(f"ok      {path.name} -> photo {photo_id}")
    return summary


@click.command("import-album")
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--album", "slug", required=True, help="Album slug, e.g. o-week-2027.")
@click.option("--title", help="Album title (used when the album is first created).")
@click.option("--credit", help="Photographer credit applied to imported photos.")
@click.option("--event-date", help="Album date, YYYY-MM-DD (when first created).")
@with_appcontext
def import_album_cmd(folder, slug, title, credit, event_date):
    """Import every photo in FOLDER into an album, uploading to the Space."""
    conn = dbmod.connect(current_app.config["DATABASE"])
    try:
        summary = import_folder(conn, get_storage(), folder, slug, title, credit, event_date,
                                log=click.echo)
    finally:
        conn.close()
    click.echo(f"Done: {summary['imported']} imported, {summary['skipped']} skipped, "
               f"{summary['failed']} failed.")
