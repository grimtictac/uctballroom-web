import pytest
from fakes import FakeClient

from app import db
from app.storage import Storage


@pytest.fixture
def gallery(app):
    app.config["GALLERY_PAGE_SIZE"] = 3
    app.extensions["storage"] = Storage(FakeClient(), "b", "https://cdn.example")
    conn = db.connect(app.config["DATABASE"])
    conn.execute("INSERT INTO albums (slug, title, event_date) VALUES ('oweek', 'O-Week', '2027-02-20')")
    conn.execute("INSERT INTO albums (slug, title) VALUES ('empty', 'Empty')")
    for i in range(7):
        conn.execute(
            """INSERT INTO photos (album_id, base_key, width, height, taken_at, caption, credit,
                                   placeholder, original_ext, variants)
               VALUES (1, ?, 1200, 800, ?, ?, 'Sam', 'data:image/webp;base64,AAAA', 'jpg', '400,800')""",
            (f"albums/oweek/k{i}", f"2027-02-20T20:0{i}:00", f"Caption {i}"),
        )
    conn.execute("UPDATE albums SET cover_photo_id = 1 WHERE id = 1")
    conn.commit()
    conn.close()
    return app


def test_album_page_first_page_and_sentinel(gallery, client):
    html = client.get("/albums/oweek").text
    assert html.count('class="tile"') == 3
    assert 'hx-get="/albums/oweek/photos?offset=3"' in html
    assert 'sizes="360px"' in html and "--ar: 1.5000" in html
    assert "k0/400.webp" in html  # smallest variant as src


def test_fragment_paging_to_the_end(gallery, client):
    html = client.get("/albums/oweek/photos?offset=3").text
    assert html.count('class="tile"') == 3
    assert 'hx-swap-oob="true"' in html and "offset=6" in html
    last = client.get("/albums/oweek/photos?offset=6").text
    assert last.count('class="tile"') == 1
    assert "hx-get" not in last  # no more pages


def test_bad_offset_is_clamped(gallery, client):
    assert client.get("/albums/oweek/photos?offset=-5").text.count('class="tile"') == 3
    assert client.get("/albums/oweek/photos?offset=abc").status_code == 200


def test_ordering_pinned_first_then_date(gallery, client):
    conn = db.connect(gallery.config["DATABASE"])
    conn.execute("UPDATE photos SET sort_order = 1 WHERE id = 7")
    conn.commit()
    html = client.get("/albums/oweek").text
    assert html.index("k6/") < html.index("k0/") < html.index("k1/")


def test_empty_and_missing_album(gallery, client):
    assert client.get("/albums/empty").status_code == 200
    assert client.get("/albums/nope").status_code == 404
    assert client.get("/albums/nope/photos").status_code == 404


def test_photo_page(gallery, client):
    html = client.get("/photo/3").text
    assert "Caption 2" in html and "Photo: Sam" in html and "2027-02-20" in html
    assert 'property="og:image" content="https://cdn.example/albums/oweek/k2/800.webp"' in html
    assert 'href="/photo/2"' in html and 'href="/photo/4"' in html
    assert "albums/oweek/k2/original.jpg" in html
    assert client.get("/photo/999").status_code == 404


def test_photo_page_ends_have_no_prev_next(gallery, client):
    assert "Previous" not in client.get("/photo/1").text
    assert "Next" not in client.get("/photo/7").text


def test_captions_are_escaped(gallery, client):
    conn = db.connect(gallery.config["DATABASE"])
    conn.execute("UPDATE photos SET caption = '<script>x</script>' WHERE id = 1")
    conn.commit()
    assert "<script>x</script>" not in client.get("/photo/1").text


def test_album_page_wires_lightbox(gallery, client):
    html = client.get("/albums/oweek").text
    assert "vendor/photoswipe/photoswipe.css" in html and "js/gallery.js" in html
    assert 'data-photo-id="1"' in html and 'data-page="/photo/1"' in html
    assert 'data-original="https://cdn.example/albums/oweek/k0/original.jpg"' in html
    assert 'data-caption="Caption 0"' in html and 'data-credit="Sam"' in html
    assert 'data-pswp-src="https://cdn.example/albums/oweek/k0/800.webp"' in html
    assert 'data-pswp-width="800"' in html and 'data-pswp-height="533"' in html
