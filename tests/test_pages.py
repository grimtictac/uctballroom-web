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


@pytest.mark.parametrize("path", ["/"])
def test_pages_render(storage, client, path):
    html = client.get(path).text
    assert "UCT Ballroom" in html
    assert "theme-toggle" not in html and "js/site.js" not in html
    assert 'data-theme="dark"' in html  # dark by default
    assert "fonts/dm-sans-latin-wght-normal.woff2" in html


def test_home_has_only_the_headline(storage, client):
    html = client.get("/").text
    assert "Got two left feet, we'll put one right!" in html
    for gone in ("Dance with us", "Ballroom &amp; Latin society", "Weekly lessons", "See the gallery",
                 "Latest albums", "All albums", "Photos are on their way", "New to ballroom"):
        assert gone not in html


def test_home_hero_is_always_the_stall_photo(app, storage, client):
    add_album(app, "new", "New Album", "2027-02-20")
    html = client.get("/").text
    assert 'class="hero-media"' in html and "img/tent-1440.webp" in html
    assert "albums/new/k/1600.webp" not in html  # album covers never become the hero
    assert 'fetchpriority="high"' in html


def test_home_lists_every_album(app, storage, client):
    for i in range(5):
        add_album(app, f"a{i}", f"Album {i}", f"2027-01-0{i + 1}")
    html = client.get("/").text
    assert html.count('class="album-card"') == 5


def test_albums_url_redirects_home(client):
    r = client.get("/albums/")
    assert r.status_code == 302 and r.headers["Location"] == "/"


def test_album_without_cover_still_renders(app, storage, client):
    add_album(app, "bare", "Bare", with_cover=False)
    assert client.get("/").status_code == 200
    assert "Bare" in client.get("/").text


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
