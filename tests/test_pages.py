import pytest
from fakes import FakeClient

from app import db
from app.storage import Storage


@pytest.fixture
def storage(app):
    app.extensions["storage"] = Storage(FakeClient(), "b", "https://cdn.example")


def add_photos(app, slug, n, title="Home"):
    conn = db.connect(app.config["DATABASE"])
    aid = conn.execute("INSERT INTO albums (slug, title) VALUES (?, ?)", (slug, title)).lastrowid
    for i in range(n):
        conn.execute(
            """INSERT INTO photos (album_id, base_key, width, height, placeholder, original_ext, variants)
               VALUES (?, ?, 1800, 1200, 'data:image/webp;base64,AAAA', 'jpg', '400,800,1600')""",
            (aid, f"albums/{slug}/k{i}"),
        )
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
    assert "Got two left feet?<br>We'll make one right!" in html
    for gone in ("Dance with us", "Ballroom &amp; Latin society", "Weekly lessons", "See the gallery",
                 "Latest albums", "All albums", "Photos are on their way", "New to ballroom"):
        assert gone not in html


def test_home_hero_is_always_the_stall_photo(app, storage, client):
    add_photos(app, "home", 1)
    html = client.get("/").text
    assert 'class="hero-media"' in html and "img/hero-1148.webp" in html
    assert 'fetchpriority="high"' in html


def test_home_shows_home_album_photos_edge_to_edge(app, storage, client):
    add_photos(app, "home", 3)
    add_photos(app, "other", 2, title="Other")
    html = client.get("/").text
    assert html.count('class="tile"') == 3 and "grid--bleed" in html
    assert "albums/home/k0/" in html and "albums/other/" not in html  # only the home album
    assert "js/gallery.js" in html and "photoswipe.css" in html


def test_home_pages_in_more_photos(app, storage, client):
    app.config["GALLERY_PAGE_SIZE"] = 2
    add_photos(app, "home", 5)
    html = client.get("/").text
    assert html.count('class="tile"') == 2
    assert 'hx-get="/albums/home/photos?offset=2"' in html


def test_home_without_a_home_album_is_just_the_hero(storage, client):
    html = client.get("/").text
    assert 'id="grid"' not in html and "Got two left feet" in html


def test_albums_url_redirects_home(client):
    r = client.get("/albums/")
    assert r.status_code == 302 and r.headers["Location"] == "/"


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
