import re

import pytest
from fakes import FakeClient
from werkzeug.security import generate_password_hash

from app import db
from app.storage import Storage

PASSWORD = "correct horse battery"


@pytest.fixture
def client_fake(app):
    fake = FakeClient()
    app.extensions["storage"] = Storage(fake, "b", "https://cdn.example")
    return fake


@pytest.fixture
def adm(app, client_fake):
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    return app


def make_album(app, slug, n, title=None):
    conn = db.connect(app.config["DATABASE"])
    aid = conn.execute("INSERT INTO albums (slug, title) VALUES (?, ?)", (slug, title or slug)).lastrowid
    for i in range(n):
        conn.execute(
            """INSERT INTO photos (album_id, base_key, width, height, placeholder, original_ext, variants,
                                   source_sha256, sort_order)
               VALUES (?, ?, 1800, 1200, 'data:image/webp;base64,AAAA', 'jpg', '400,800', ?, ?)""",
            (aid, f"albums/{slug}/k{i}", f"sha-{slug}-{i}", i + 1))
    conn.commit()
    conn.close()


def query(app, sql, *args):
    conn = db.connect(app.config["DATABASE"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def sign_in(client, password=PASSWORD, ip="1.2.3.4"):
    page = client.get("/admin", headers={"X-Forwarded-For": ip})
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    return client.post("/admin", data={"password": password, "csrf": csrf}, headers={"X-Forwarded-For": ip})


def token(client):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get("/albums/").text).group(1)


def post(client, url, body):
    return client.post(url, json=body, headers={"X-CSRF-Token": token(client)})


def test_admin_is_off_without_a_password(app, client_fake, client):
    assert client.get("/admin").status_code == 404


def test_wrong_then_right_password(adm, client):
    assert sign_in(client, "nope").status_code == 401
    assert "Admin" not in client.get("/albums/").text
    r = sign_in(client)
    assert r.status_code == 302
    html = client.get("/albums/").text
    assert 'id="admin-bar"' in html and "data-admin" in html and "js/admin.js" in html


def test_repeated_wrong_passwords_are_throttled_per_address(adm, client):
    for _ in range(6):
        assert sign_in(client, "nope").status_code == 401
    assert sign_in(client).status_code == 429  # even the right password is refused for a while
    assert sign_in(client, ip="9.9.9.9").status_code == 302  # another visitor is unaffected


def test_login_needs_the_csrf_token(adm, client):
    client.get("/admin")
    assert client.post("/admin", data={"password": PASSWORD}).status_code == 400


def test_admin_endpoints_look_missing_when_signed_out(adm, client):
    for url, body in (("/admin/photos/delete", {"ids": [1]}), ("/admin/albums/order", {"slugs": []}),
                      ("/admin/albums/x/highlights", {"add": [1]}), ("/admin/albums/x/crop", {}),
                      ("/admin/settings", {"mode": "fade", "seconds": 4})):
        assert client.post(url, json=body).status_code == 404


def test_signed_in_posts_need_csrf(adm, client):
    make_album(adm, "a", 1)
    sign_in(client)
    assert client.post("/admin/photos/delete", json={"ids": [1]}).status_code == 400
    assert query(adm, "SELECT COUNT(*) FROM photos")[0][0] == 1


def test_public_pages_have_no_admin_bits_and_no_cookie(adm, client):
    make_album(adm, "a", 1)
    r = client.get("/albums/a")
    assert "admin-bar" not in r.text and "data-admin" not in r.text and "cover-data" not in r.text
    assert "Set-Cookie" not in r.headers


def test_delete_removes_rows_files_and_leaves_a_tombstone(adm, client_fake, client):
    make_album(adm, "a", 3)
    make_album(adm, "b", 1)
    for key in ("albums/a/k0/400.webp", "albums/a/k0/800.webp", "albums/a/k0/original.jpg", "albums/b/k0/400.webp"):
        client_fake.objects[key] = {}
    sign_in(client)
    r = post(client, "/admin/photos/delete", {"ids": [1]})
    assert r.get_json() == {"deleted": 1, "files_left_behind": False}
    assert [row[0] for row in query(adm, "SELECT id FROM photos ORDER BY id")] == [2, 3, 4]
    assert set(client_fake.deleted) == {"albums/a/k0/400.webp", "albums/a/k0/800.webp", "albums/a/k0/original.jpg"}
    assert "albums/b/k0/400.webp" in client_fake.objects
    assert query(adm, "SELECT source_sha256 FROM removed_photos")[0][0] == "sha-a-0"


def test_deleted_photos_are_not_re_imported(adm, client):
    import json

    from app.importer import album_state_cmd
    make_album(adm, "a", 2)
    sign_in(client)
    post(client, "/admin/photos/delete", {"ids": [1]})
    state = json.loads(adm.test_cli_runner().invoke(args=[album_state_cmd.name, "a"]).output)
    assert "sha-a-0" in state["hashes"] and "sha-a-1" in state["hashes"]
    rec = {"base_key": "albums/a/new", "width": 1, "height": 1, "taken_at": None, "placeholder": "p",
           "original_ext": "jpg", "variants": "400", "sha256": "sha-a-0", "sort_order": 9}
    out = adm.test_cli_runner().invoke(args=["register-photos", "--album", "a"], input=json.dumps(rec) + "\n")
    assert out.output.strip() == "registered 0, skipped 1"


def test_delete_validates_input_and_survives_storage_failure(adm, client_fake, client, monkeypatch):
    make_album(adm, "a", 2)
    sign_in(client)
    for bad in ({}, {"ids": []}, {"ids": ["1"]}, {"ids": list(range(501))}):
        assert post(client, "/admin/photos/delete", bad).status_code == 400
    monkeypatch.setattr(client_fake, "delete_objects", lambda **kw: (_ for _ in ()).throw(OSError("down")))
    r = post(client, "/admin/photos/delete", {"ids": [1]})
    assert r.get_json() == {"deleted": 1, "files_left_behind": True}
    assert query(adm, "SELECT COUNT(*) FROM photos")[0][0] == 1  # the site is already consistent








def test_reorder_albums(adm, client):
    for slug in ("apple", "banana", "cherry"):
        make_album(adm, slug, 1)
    def order():
        return [m for m in re.findall(r'href="/albums/(\w+)"', client.get("/albums/").text)]
    assert order() == ["apple", "banana", "cherry"]  # alphabetical until you reorder
    sign_in(client)
    assert post(client, "/admin/albums/order", {"slugs": ["cherry", "apple"]}).status_code == 200
    assert order() == ["cherry", "apple", "banana"]  # ordered ones first, the rest after
    assert post(client, "/admin/albums/order", {"slugs": "x"}).status_code == 400


def test_a_card_with_no_highlights_shows_its_cover_then_the_first_photo(adm, client):
    make_album(adm, "a", 2)
    sign_in(client)
    assert 'class="slide is-active"' in client.get("/albums/").text.replace("slide is-active", 'slide is-active')
    assert "albums/a/k0/" in client.get("/albums/").text
    conn = db.connect(adm.config["DATABASE"])
    conn.execute("UPDATE albums SET cover_photo_id = 2")
    conn.commit()
    conn.close()
    post(client, "/admin/photos/delete", {"ids": [2]})  # the cover photo is deleted
    html = client.get("/albums/").text
    assert "albums/a/k0/" in html and "1 photo<" in html





def test_highlights_add_remove_and_order(adm, client):
    make_album(adm, "a", 4)
    make_album(adm, "b", 2)
    sign_in(client)
    r = post(client, "/admin/albums/a/highlights", {"add": [3, 1, 3]})
    assert r.get_json() == {"ok": True, "highlights": [3, 1]}  # given order kept, repeats dropped
    assert post(client, "/admin/albums/a/highlights", {"add": [4]}).get_json()["highlights"] == [3, 1, 4]
    assert post(client, "/admin/albums/a/highlights", {"remove": [1]}).get_json()["highlights"] == [3, 4]
    assert post(client, "/admin/albums/a/highlights", {"add": [5]}).status_code == 400  # photo 5 is in album b
    for bad in ({"add": ["1"]}, {"add": 1}, {"remove": [True]}):
        assert post(client, "/admin/albums/a/highlights", bad).status_code == 400
    assert post(client, "/admin/albums/nope/highlights", {"add": [1]}).status_code == 404


def test_highlight_limit(adm, client, monkeypatch):
    from app.gallery import queries
    monkeypatch.setattr(queries, "MAX_HIGHLIGHTS", 3)
    make_album(adm, "a", 6)
    sign_in(client)
    assert post(client, "/admin/albums/a/highlights", {"add": [1, 2, 3]}).status_code == 200
    r = post(client, "/admin/albums/a/highlights", {"add": [4]})
    assert r.status_code == 400 and "at most 3" in r.get_json()["error"]
    assert post(client, "/admin/albums/a/highlights", {"remove": [1], "add": [4]}).status_code == 200


def test_a_big_album_can_have_a_highlight_per_couple(adm, client):
    make_album(adm, "a", 60)
    sign_in(client)
    assert post(client, "/admin/albums/a/highlights", {"add": list(range(1, 61))}).status_code == 200


def test_cards_load_only_the_first_slide_up_front(adm, client):
    make_album(adm, "a", 5)
    sign_in(client)
    post(client, "/admin/settings", {"mode": "fade", "seconds": 4, "shuffle": False})
    post(client, "/admin/albums/a/highlights", {"add": [1, 2, 3, 4]})
    html = client.get("/albums/").text
    assert html.count(' src="https://cdn.example/albums/a/') == 1 and html.count("data-src=") == 3


def test_highlights_drive_the_card_and_the_tile_badges(adm, client):
    make_album(adm, "a", 3)
    sign_in(client)
    post(client, "/admin/settings", {"mode": "fade", "seconds": 4, "shuffle": False})
    post(client, "/admin/albums/a/highlights", {"add": [3, 2]})
    html = client.get("/albums/").text
    assert html.count('class="slide') == 2  # only the highlights, not photo 1
    assert html.index("albums/a/k2/") < html.index("albums/a/k1/") and "albums/a/k0/" not in html
    assert html.count("is-active") == 1 and html.index("is-active") < html.index("albums/a/k1/")
    album = client.get("/albums/a").text
    assert album.count(" data-highlight") == 2
    assert 'id="highlight-data"' in album and '"fallback": false' in album.replace('"fallback":false', '"fallback": false')


def test_deleting_a_photo_removes_it_from_the_highlights(adm, client):
    make_album(adm, "a", 3)
    sign_in(client)
    post(client, "/admin/albums/a/highlights", {"add": [1, 2]})
    post(client, "/admin/photos/delete", {"ids": [1]})
    assert [r[0] for r in query(adm, "SELECT photo_id FROM album_highlights")] == [2]


def test_each_highlight_keeps_its_own_framing(adm, client):
    make_album(adm, "a", 2)
    sign_in(client)
    post(client, "/admin/albums/a/highlights", {"add": [1, 2]})
    r = post(client, "/admin/albums/a/crop", {"fit": "crop", "items": [{"photo_id": 1, "x": 10, "y": 80}, {"photo_id": 2, "x": 60.5, "y": 5}]})
    assert r.status_code == 200
    html = client.get("/albums/").text
    assert "object-position: 10.0% 80.0%" in html and "object-position: 60.5% 5.0%" in html


def test_framing_the_fallback_cover_and_the_uncropped_option(adm, client):
    make_album(adm, "a", 1)
    sign_in(client)
    assert post(client, "/admin/albums/a/crop", {"fit": "crop", "x": 20.5, "y": 10}).status_code == 200
    html = client.get("/albums/").text
    assert "object-position: 20.5% 10.0%" in html and "cover--full" not in html
    post(client, "/admin/albums/a/crop", {"fit": "full", "x": 50, "y": 50})
    assert "cover--full" in client.get("/albums/").text


def test_crop_validation(adm, client):
    make_album(adm, "a", 1)
    sign_in(client)
    bad = ({"fit": "zoom", "x": 1, "y": 1}, {"fit": "crop", "x": 120, "y": 5}, {"fit": "crop", "x": "a", "y": 5},
           {"fit": "crop", "items": "no"}, {"fit": "crop", "items": [{"photo_id": "1", "x": 1, "y": 1}]},
           {"fit": "crop", "items": [{"photo_id": 1, "x": 1, "y": 101}]})
    for body in bad:
        assert post(client, "/admin/albums/a/crop", body).status_code == 400


def test_settings_default_save_and_validate(adm, client):
    make_album(adm, "a", 1)
    assert 'data-mode="fade" data-seconds="4.0"' in client.get("/albums/").text
    sign_in(client)
    assert post(client, "/admin/settings", {"mode": "slide", "seconds": 6.5}).status_code == 200
    assert post(client, "/admin/settings", {"mode": "slide", "seconds": 6.5, "shuffle": "yes"}).status_code == 400
    assert 'data-mode="slide" data-seconds="6.5"' in client.get("/albums/").text
    for bad in ({"mode": "spin", "seconds": 4}, {"mode": "fade", "seconds": 0.5}, {"mode": "fade", "seconds": 99},
                {"mode": "fade", "seconds": "fast"}, {"mode": "fade"}):
        assert post(client, "/admin/settings", bad).status_code == 400
    assert 'data-mode="slide"' in client.get("/albums/").text  # unchanged by the bad requests


def test_shuffle_is_on_by_default_and_can_be_switched_off(adm, client, monkeypatch):
    import random
    make_album(adm, "a", 4)
    sign_in(client)
    post(client, "/admin/albums/a/highlights", {"add": [1, 2, 3, 4]})
    monkeypatch.setattr(random, "shuffle", lambda seq: seq.reverse())  # a visible, repeatable "shuffle"

    def order():
        html = client.get("/albums/").text
        return [html.index(f"albums/a/k{i}/") for i in range(4)]

    assert order() == sorted(order(), reverse=True)  # default: shuffled (here, reversed)
    assert post(client, "/admin/settings", {"mode": "fade", "seconds": 4, "shuffle": False}).status_code == 200
    assert order() == sorted(order())  # off: the order you added them in
    post(client, "/admin/settings", {"mode": "fade", "seconds": 4})  # leaving shuffle out changes nothing
    assert order() == sorted(order())
    assert post(client, "/admin/settings", {"mode": "fade", "seconds": 4, "shuffle": True}).status_code == 200
    assert order() == sorted(order(), reverse=True)


def test_shuffle_changes_which_photo_is_first_across_visits(adm, client):
    make_album(adm, "a", 6)
    sign_in(client)
    post(client, "/admin/albums/a/highlights", {"add": [1, 2, 3, 4, 5, 6]})
    firsts = set()
    for _ in range(40):
        html = client.get("/albums/").text
        firsts.add(re.search(r'class="slide is-active" src="[^"]*albums/a/(k\d)/', html).group(1))
    assert len(firsts) >= 3  # not always the same photo up front


def test_panel_is_admin_only_and_shows_the_current_settings(adm, client):
    make_album(adm, "a", 2)
    assert client.get("/admin/panel").status_code == 404
    sign_in(client)
    post(client, "/admin/albums/a/highlights", {"add": [1, 2]})
    post(client, "/admin/settings", {"mode": "slide", "seconds": 7})
    html = client.get("/admin/panel").text
    assert 'value="slide" checked' in html and 'value="7"' in html
    assert 'class="album-list panel-preview"' in html and html.count('class="slide') == 2


def test_album_page_offers_framing_data_only_to_the_admin(adm, client):
    make_album(adm, "a", 2)
    assert "highlight-data" not in client.get("/albums/a").text
    sign_in(client)
    html = client.get("/albums/a").text
    assert 'id="highlight-data"' in html and '"slug": "a"' in html.replace('"slug":"a"', '"slug": "a"')
    assert 'draggable="true" data-slug="a"' in client.get("/albums/").text


def test_logout(adm, client):
    sign_in(client)
    client.post("/admin/logout", data={"csrf": token(client)})
    assert "admin-bar" not in client.get("/albums/").text


def test_a_missing_token_never_matches_a_missing_session_token(adm, client):
    make_album(adm, "a", 1)
    sign_in(client)  # sign-in clears the session, so no CSRF token exists yet
    assert client.post("/admin/photos/delete", json={"ids": [1]}).status_code == 400
    assert client.post("/admin/photos/delete", json={"ids": [1]}, headers={"X-CSRF-Token": ""}).status_code == 400
    assert query(adm, "SELECT COUNT(*) FROM photos")[0][0] == 1
