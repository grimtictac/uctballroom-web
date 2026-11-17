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
                      ("/admin/albums/x/cover", {"photo_id": 1}), ("/admin/albums/x/crop", {})):
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


def test_set_cover_resets_framing_and_rejects_other_albums_photos(adm, client):
    make_album(adm, "a", 2)
    make_album(adm, "b", 1)
    sign_in(client)
    post(client, "/admin/albums/a/crop", {"x": 10, "y": 90, "fit": "full"})
    assert post(client, "/admin/albums/a/cover", {"photo_id": 2}).status_code == 200
    row = query(adm, "SELECT cover_photo_id, cover_x, cover_y, cover_fit FROM albums WHERE slug = 'a'")[0]
    assert tuple(row) == (2, 50, 35, "crop")
    assert post(client, "/admin/albums/a/cover", {"photo_id": 3}).status_code == 400  # photo 3 is in album b
    assert post(client, "/admin/albums/nope/cover", {"photo_id": 1}).status_code == 404


def test_crop_is_validated_and_rendered(adm, client):
    make_album(adm, "a", 1)
    sign_in(client)
    for bad in ({"x": 120, "y": 5, "fit": "crop"}, {"x": 5, "y": 5, "fit": "zoom"}, {"x": "a", "y": 5, "fit": "crop"}):
        assert post(client, "/admin/albums/a/crop", bad).status_code == 400
    assert post(client, "/admin/albums/a/crop", {"x": 20.5, "y": 10, "fit": "crop"}).status_code == 200
    html = client.get("/albums/").text
    assert "--x: 20.5%; --y: 10.0%" in html and "cover--full" not in html
    post(client, "/admin/albums/a/crop", {"x": 50, "y": 50, "fit": "full"})
    assert "cover--full" in client.get("/albums/").text


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


def test_cover_falls_back_to_the_first_photo_when_the_cover_is_deleted(adm, client):
    make_album(adm, "a", 2)
    sign_in(client)
    post(client, "/admin/albums/a/cover", {"photo_id": 2})
    post(client, "/admin/photos/delete", {"ids": [2]})
    html = client.get("/albums/").text
    assert "albums/a/k0/" in html and "1 photo<" in html


def test_album_page_offers_cover_tools_only_to_the_admin(adm, client):
    make_album(adm, "a", 2)
    assert "cover-data" not in client.get("/albums/a").text
    sign_in(client)
    html = client.get("/albums/a").text
    assert 'id="cover-data"' in html and 'data-slug="a"' in html and 'data-fit="crop"' in html
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
