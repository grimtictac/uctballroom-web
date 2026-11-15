import pytest
from fakes import FakeClient

from app import db
from app.storage import Storage


@pytest.fixture
def storage(app):
    app.extensions["storage"] = Storage(FakeClient(), "b", "https://cdn.example")


def add_album(app, slug, title, date=None, with_cover=True):
    conn = db.connect(app.config["DATABASE"])
    aid = conn.execute(
        "INSERT INTO albums (slug, title, event_date) VALUES (?, ?, ?)", (slug, title, date)
    ).lastrowid
    if with_cover:
        pid = conn.execute(
            """INSERT INTO photos (album_id, base_key, width, height, placeholder, original_ext, variants)
               VALUES (?, ?, 1800, 1200, 'data:image/webp;base64,AAAA', 'jpg', '400,800,1600')""",
            (aid, f"albums/{slug}/k"),
        ).lastrowid
        conn.execute("UPDATE albums SET cover_photo_id = ? WHERE id = ?", (pid, aid))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("path", ["/", "/albums/"])
def test_pages_render(storage, client, path):
    html = client.get(path).text
    assert "UCT Ballroom" in html
    assert "theme-toggle" not in html and "js/site.js" not in html
    assert 'data-theme="dark"' in html  # dark by default
    assert "fonts/dm-sans-latin-wght-normal.woff2" in html


def test_home_without_albums_has_no_hero_image(storage, client):
    html = client.get("/").text
    assert "Dance with us." in html and "Photos are on their way." in html
    assert "New to ballroom" not in html
    assert 'fetchpriority="high"' not in html


def test_home_uses_latest_album_cover_as_hero(app, storage, client):
    add_album(app, "old", "Old Album", "2026-01-01")
    add_album(app, "new", "New Album", "2027-02-20")
    html = client.get("/").text
    assert 'fetchpriority="high"' in html
    assert "albums/new/k/1600.webp" in html  # hero is the newest album's cover
    assert "New Album" in html and "Old Album" in html


def test_home_limits_to_three_albums(app, storage, client):
    for i in range(5):
        add_album(app, f"a{i}", f"Album {i}", f"2027-01-0{i + 1}")
    html = client.get("/").text
    assert html.count('class="album-card"') == 3


def test_album_without_cover_still_renders(app, storage, client):
    add_album(app, "bare", "Bare", with_cover=False)
    assert client.get("/").status_code == 200
    assert "Bare" in client.get("/albums/").text


def test_removed_pages_are_gone(storage, client):
    for path in ("/about", "/join", "/events"):
        assert client.get(path).status_code == 404


def test_no_top_menu_links(storage, client):
    html = client.get("/").text
    assert 'aria-label="Main"' not in html and 'aria-label="Footer"' not in html


def test_footer_year(storage, client):
    from datetime import date

    assert f"&copy; {date.today().year} UCT Ballroom" in client.get("/").text


def test_logo_and_icons_wired_in(storage, client, app):
    from pathlib import Path

    html = client.get("/").text
    assert 'class="logo-mark"' in html
    for f in ("favicon-32.png", "icon-192.png", "apple-touch-icon.png"):
        assert f"img/{f}" in html
        assert client.get(f"/static/img/{f}").status_code == 200
    assert client.get("/static/img/logo-mark.png").status_code == 200
    assert 'url("../img/logo-mark.png")' in (Path(app.root_path) / "static/css/site.css").read_text()
