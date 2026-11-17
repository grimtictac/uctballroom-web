import sqlite3

import pytest
from fakes import FakeClient
from test_images import make_jpeg

from app import db
from app.importer import get_or_create_album, import_folder
from app.storage import Storage


def setup(app, tmp_path, n=2):
    folder = tmp_path / "photos"
    folder.mkdir()
    for i in range(n):
        (folder / f"p{i}.jpg").write_bytes(make_jpeg((1000 + i, 800), taken=f"2025:03:0{i+1} 20:00:00"))
    (folder / "notes.txt").write_text("ignore me")
    (folder / "bad.jpg").write_bytes(b"corrupt")
    client = FakeClient()
    return folder, client, Storage(client, "b", "https://cdn.example"), db.connect(app.config["DATABASE"])


def test_import_folder(app, tmp_path):
    folder, client, storage, conn = setup(app, tmp_path)
    summary = import_folder(conn, storage, folder, "o-week", "O-Week", credit="Sam")
    assert summary == {"imported": 2, "skipped": 0, "failed": 1}
    rows = conn.execute("SELECT * FROM photos ORDER BY taken_at").fetchall()
    assert [r["taken_at"][:10] for r in rows] == ["2025-03-01", "2025-03-02"]
    assert rows[0]["credit"] == "Sam" and rows[0]["variants"] == "400,800"
    base = rows[0]["base_key"]
    assert base.startswith("albums/o-week/")
    assert {f"{base}/400.webp", f"{base}/800.webp", f"{base}/original.jpg"} <= set(client.objects)
    album = conn.execute("SELECT * FROM albums WHERE slug = 'o-week'").fetchone()
    assert album["title"] == "O-Week" and album["cover_photo_id"] == rows[0]["id"]


def test_reimport_skips_duplicates(app, tmp_path):
    folder, client, storage, conn = setup(app, tmp_path)
    import_folder(conn, storage, folder, "o-week")
    uploads = len(client.objects)
    summary = import_folder(conn, storage, folder, "o-week")
    assert summary == {"imported": 0, "skipped": 2, "failed": 1}
    assert len(client.objects) == uploads
    assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 2


def test_failed_db_insert_cleans_up_uploads(app, tmp_path):
    folder, client, storage, conn = setup(app, tmp_path, n=1)
    conn.execute("DROP INDEX idx_photos_album_sha")
    conn.execute("ALTER TABLE photos DROP COLUMN variants")  # force INSERT to fail
    with pytest.raises(sqlite3.OperationalError):
        import_folder(conn, storage, folder, "o-week")
    assert client.objects == {}


def test_bad_slug_rejected(app):
    conn = db.connect(app.config["DATABASE"])
    with pytest.raises(ValueError):
        get_or_create_album(conn, "Bad Slug")


def test_photos_follow_filename_order_not_date_taken(app, tmp_path):
    from app.gallery import queries

    folder = tmp_path / "ordered"
    folder.mkdir()
    # Filename order is the reverse of date-taken order.
    (folder / "001.jpg").write_bytes(make_jpeg((1000, 800), taken="2025:09:01 20:00:00"))
    (folder / "002.jpg").write_bytes(make_jpeg((1001, 800), taken="2025:01:01 20:00:00"))
    client = FakeClient()
    storage, conn = Storage(client, "b", "https://cdn.example"), db.connect(app.config["DATABASE"])
    import_folder(conn, storage, folder, "home")
    album = queries.get_album(conn, "home")
    photos, _ = queries.album_photos(conn, album["id"], 10, 0)
    assert [p["width"] for p in photos] == [1000, 1001]

    # A later batch is appended after what is already there.
    later = tmp_path / "later"
    later.mkdir()
    (later / "000.jpg").write_bytes(make_jpeg((1002, 800), taken="2020:01:01 20:00:00"))
    import_folder(conn, storage, later, "home")
    photos, _ = queries.album_photos(conn, album["id"], 10, 0)
    assert [p["width"] for p in photos] == [1000, 1001, 1002]


def test_register_photos_and_album_state_roundtrip(app):
    import json

    from app.importer import album_state_cmd
    rec = {"base_key": "albums/x/aa", "width": 10, "height": 8, "taken_at": None, "placeholder": "p",
           "original_ext": "jpg", "variants": "400", "sha256": "h1", "sort_order": 3}
    runner = app.test_cli_runner()
    stdin = json.dumps(rec) + "\n" + json.dumps({**rec, "base_key": "albums/x/bb", "sha256": "h2",
                                                  "sort_order": 4}) + "\n"
    out = runner.invoke(args=["register-photos", "--album", "x", "--title", "X"], input=stdin)
    assert out.output.strip() == "registered 2, skipped 0"
    again = runner.invoke(args=["register-photos", "--album", "x"], input=stdin)  # idempotent
    assert again.output.strip() == "registered 0, skipped 2"
    state = json.loads(runner.invoke(args=[album_state_cmd.name, "x"]).output)
    assert state["exists"] and state["max_order"] == 4 and sorted(state["hashes"]) == ["h1", "h2"]
    assert json.loads(runner.invoke(args=["album-state", "nope"]).output)["exists"] is False
    conn = db.connect(app.config["DATABASE"])
    album = conn.execute("SELECT * FROM albums WHERE slug = 'x'").fetchone()
    assert album["title"] == "X" and album["cover_photo_id"] is not None
