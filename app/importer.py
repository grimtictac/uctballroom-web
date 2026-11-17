"""Import a folder of photos into an album: process, upload to the Space, insert rows."""
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

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


def photo_base_key(slug, filename, sha):
    """Readable, collision-proof folder for one photo: the cleaned file name plus a short content
    fingerprint, e.g. albums/iv26-medals/08-37-45-000_IMG_3662-6cef721b. A re-exported file gets a new
    key (the files are cached as immutable) and re-importing the same file lands on the same key."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._-")[:80] or "photo"
    return f"albums/{slug}/{stem}-{sha[:8]}"


def download_disposition(filename):
    """Content-Disposition that makes the original download under its real file name."""
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def record_of(p, base_key, sha, sort_order):
    """The database fields of a processed photo (also the wire format for register-photos)."""
    return {"base_key": base_key, "width": p.width, "height": p.height, "taken_at": p.taken_at,
            "placeholder": p.placeholder, "original_ext": p.original_ext,
            "variants": ",".join(map(str, p.variants)), "sha256": sha, "sort_order": sort_order}


def upload_photo(storage, base_key, p, filename=None):
    """Upload every variant plus the original. Returns the bytes sent."""
    sent = 0
    for width, blob in p.variants.items():
        storage.upload(f"{base_key}/{width}.webp", blob, "image/webp")
        sent += len(blob)
    storage.upload(f"{base_key}/original.{p.original_ext}", p.original, CONTENT_TYPE[p.original_ext],
                   content_disposition=download_disposition(filename) if filename else None)
    return sent + len(p.original)


def insert_photo(conn, album_id, rec, credit=None):
    """Insert one photo row (and make it the album cover if the album has none). Commits."""
    cur = conn.execute(
        """INSERT INTO photos (album_id, base_key, width, height, credit, taken_at,
                               placeholder, original_ext, variants, source_sha256, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (album_id, rec["base_key"], rec["width"], rec["height"], credit, rec["taken_at"],
         rec["placeholder"], rec["original_ext"], rec["variants"], rec["sha256"], rec["sort_order"]),
    )
    conn.execute(
        "UPDATE albums SET cover_photo_id = ? WHERE id = ? AND cover_photo_id IS NULL",
        (cur.lastrowid, album_id),
    )
    conn.commit()
    return cur.lastrowid


def import_photo(conn, storage, album_id, album_slug, data, credit=None, sort_order=None, filename=None):
    """Import one photo. Returns the new photo id, or None if it was already imported."""
    sha = hashlib.sha256(data).hexdigest()
    if conn.execute(
        """SELECT 1 FROM photos WHERE album_id = ? AND source_sha256 = ?
           UNION SELECT 1 FROM removed_photos WHERE album_id = ? AND source_sha256 = ?""",
        (album_id, sha, album_id, sha),
    ).fetchone():
        return None

    p = process(data)
    base_key = photo_base_key(album_slug, filename, sha) if filename else f"albums/{album_slug}/{secrets.token_hex(6)}"
    try:
        upload_photo(storage, base_key, p, filename)
        return insert_photo(conn, album_id, record_of(p, base_key, sha, sort_order), credit)
    except Exception:
        conn.rollback()
        storage.delete_prefix(base_key)  # don't leave orphaned files behind
        raise


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
                                    sort_order=order + 1, filename=path.name)
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


@click.command("album-state")
@click.argument("slug")
@with_appcontext
def album_state_cmd(slug):
    """Print JSON describing an album and the storage settings, for the local batch importer."""
    cfg = current_app.config
    conn = dbmod.connect(cfg["DATABASE"])
    try:
        row = conn.execute("SELECT id FROM albums WHERE slug = ?", (slug,)).fetchone()
        hashes, top = [], 0
        if row:
            hashes = [r[0] for r in conn.execute(
                """SELECT source_sha256 FROM photos WHERE album_id = ? AND source_sha256 IS NOT NULL
                   UNION SELECT source_sha256 FROM removed_photos WHERE album_id = ?""",
                (row["id"], row["id"]))]
            top = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM photos WHERE album_id = ?",
                               (row["id"],)).fetchone()[0]
    finally:
        conn.close()
    click.echo(json.dumps({"exists": bool(row), "max_order": top, "hashes": hashes,
                           "bucket": cfg.get("SPACES_BUCKET"), "region": cfg.get("SPACES_REGION"),
                           "prefix": cfg.get("SPACES_PREFIX", "")}))


@click.command("register-photos")
@click.option("--album", "slug", required=True)
@click.option("--title")
@with_appcontext
def register_photos_cmd(slug, title):
    """Insert photo rows for files already uploaded to the Space. Reads JSON lines from stdin."""
    conn = dbmod.connect(current_app.config["DATABASE"])
    registered = skipped = 0
    try:
        album_id = get_or_create_album(conn, slug, title)
        for line in sys.stdin:
            if not line.strip():
                continue
            rec = json.loads(line)
            if conn.execute(
                    """SELECT 1 FROM photos WHERE album_id = ? AND source_sha256 = ?
                       UNION SELECT 1 FROM removed_photos WHERE album_id = ? AND source_sha256 = ?""",
                    (album_id, rec["sha256"], album_id, rec["sha256"])).fetchone():
                skipped += 1
                continue
            insert_photo(conn, album_id, rec)
            registered += 1
    finally:
        conn.close()
    click.echo(f"registered {registered}, skipped {skipped}")
