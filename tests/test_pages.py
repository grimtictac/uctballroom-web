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


@pytest.mark.parametrize("path", ["/", "/about", "/join", "/events", "/albums/"])
def test_pages_render(storage, client, path):
    html = client.get(path).text
    assert "UCT Ballroom" in html
    assert 'id="theme-toggle"' in html and "js/site.js" in html
    assert 'data-theme="dark"' in html  # dark by default
    assert "fonts/inter-latin-wght-normal.woff2" in html


def test_home_without_albums_has_no_hero_image(storage, client):
    html = client.get("/").text
    assert "Dance with us." in html and "Photos are on their way." in html
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


@pytest.mark.parametrize(
    "path,label",
    [("/albums/", "Gallery"), ("/events", "Events"), ("/about", "About"), ("/join", "Join us")],
)
def test_nav_marks_current_page(storage, client, path, label):
    html = client.get(path).text
    assert html.count('aria-current="page"') == 1
    assert f'aria-current="page">{label}</a>' in html


def test_home_marks_no_nav_item_current(storage, client):
    assert 'aria-current="page"' not in client.get("/").text


def test_footer_year(storage, client):
    from datetime import date

    assert f"&copy; {date.today().year} UCT Ballroom" in client.get("/about").text
