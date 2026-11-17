import json
import re

import pytest
from fakes import FakeClient
from test_admin import PASSWORD, make_album, post, query, sign_in
from werkzeug.security import generate_password_hash

from app import db, people
from app.storage import Storage


@pytest.fixture
def adm(app):
    app.extensions["storage"] = Storage(FakeClient(), "b", "https://cdn.example")
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    return app


def names(html):
    return re.findall(r'<h2 class="group-names[^"]*">([^<]*)</h2>', html)


def start_group(client, slug, photo_id, dancers=None):
    gid = post(client, f"/admin/albums/{slug}/groups", {"start": photo_id}).get_json()["group"]
    if dancers is not None:
        post(client, f"/admin/groups/{gid}/people", {"names": dancers})
    return gid


def test_slugify_and_names():
    assert people.clean_name("  Zoë   Smith ") == "Zoë Smith"
    assert people.slugify("Zoë  Smith") == "zoe-smith"
    assert people.slugify("李明").startswith("person-") and people.slugify("李明") == people.slugify("李明")


def test_groups_start_at_a_marker_and_run_to_the_next(adm, client):
    from app.gallery import queries
    make_album(adm, "a", 6)
    sign_in(client)
    g1 = start_group(client, "a", 2)
    g2 = start_group(client, "a", 5)
    conn = db.connect(adm.config["DATABASE"])
    ids, group_of, groups = queries.album_layout(conn, 1)
    assert ids == [1, 2, 3, 4, 5, 6]
    assert group_of == {1: None, 2: g1, 3: g1, 4: g1, 5: g2, 6: g2}  # photo 1 is before any marker
    assert groups[g1]["start"] == 2 and groups[g2]["people"] == []


def test_adding_the_same_marker_twice_changes_nothing(adm, client):
    make_album(adm, "a", 3)
    sign_in(client)
    assert start_group(client, "a", 2) == start_group(client, "a", 2)
    assert query(adm, "SELECT COUNT(*) FROM photo_groups")[0][0] == 1


def test_group_endpoint_validation_and_access(adm, client):
    make_album(adm, "a", 2)
    make_album(adm, "b", 1)
    assert client.post("/admin/albums/a/groups", json={"start": 1}).status_code == 404  # signed out
    sign_in(client)
    for bad in ({}, {"start": "1"}, {"start": True}, {"start": 3}):  # photo 3 is in album b
        assert post(client, "/admin/albums/a/groups", bad).status_code == 400
    assert post(client, "/admin/albums/nope/groups", {"start": 1}).status_code == 404
    gid = start_group(client, "a", 1)
    for bad in ({"names": "x"}, {"names": [1]}, {"names": ["a"] * 7}, {"names": ["x" * 81]}, {}):
        assert post(client, f"/admin/groups/{gid}/people", bad).status_code == 400
    assert post(client, "/admin/groups/999/people", {"names": []}).status_code == 404
    assert post(client, "/admin/groups/999/delete", {}).status_code == 404


def test_dancers_are_stored_as_reusable_person_tags(adm, client):
    make_album(adm, "a", 4)
    sign_in(client)
    g1 = start_group(client, "a", 1, ["Alice  Smith", "Bob Jones"])
    g2 = start_group(client, "a", 3, ["alice smith", "Cara Lee", " "])  # same person, new case; blank ignored
    tags = [tuple(r) for r in query(adm, "SELECT name, slug, kind FROM tags ORDER BY id")]
    assert tags == [("Alice Smith", "alice-smith", "person"), ("Bob Jones", "bob-jones", "person"),
                    ("Cara Lee", "cara-lee", "person")]  # Alice exists once, under her first spelling
    rows = query(adm, "SELECT group_id, t.name FROM group_people gp JOIN tags t ON t.id = gp.tag_id "
                      "ORDER BY group_id, position")
    assert [tuple(r) for r in rows] == [(g1, "Alice Smith"), (g1, "Bob Jones"), (g2, "Alice Smith"), (g2, "Cara Lee")]
    post(client, f"/admin/groups/{g1}/people", {"names": ["Bob Jones"]})  # replaces the list
    assert [r[0] for r in query(adm, "SELECT t.name FROM group_people gp JOIN tags t ON t.id=gp.tag_id WHERE group_id=?", g1)] == ["Bob Jones"]
    assert query(adm, "SELECT COUNT(*) FROM tags")[0][0] == 3  # nobody is forgotten when removed from a group
    assert people.all_people(db.connect(adm.config["DATABASE"])) == ["Alice Smith", "Bob Jones", "Cara Lee"]


def test_everyone_sees_group_names_above_the_group(adm, client):
    make_album(adm, "a", 6)
    sign_in(client)
    start_group(client, "a", 1, ["Alice Smith", "Bob Jones"])
    start_group(client, "a", 4, ["Cara Lee", "Dan Wu"])
    public = adm.test_client().get("/albums/a").text
    assert names(public) == ["Alice Smith &amp; Bob Jones", "Cara Lee &amp; Dan Wu"]
    assert public.index("Alice Smith") < public.index("albums/a/k0/") and public.index("Cara Lee") > public.index("albums/a/k2/")
    assert public.count('class="grid-break"') == 1  # the first group needs no break above it
    assert "group-edit" not in public and "people-list" not in public


def test_an_unnamed_group_breaks_the_row_but_only_the_admin_sees_a_heading(adm, client):
    make_album(adm, "a", 4)
    sign_in(client)
    start_group(client, "a", 3)
    assert names(adm.test_client().get("/albums/a").text) == []
    assert adm.test_client().get("/albums/a").text.count('class="grid-break"') == 1
    admin_html = client.get("/albums/a").text
    assert names(admin_html) == ["Unnamed group"] and "group-edit" in admin_html


def test_photos_before_the_first_marker_have_no_heading(adm, client):
    make_album(adm, "a", 3)
    sign_in(client)
    start_group(client, "a", 3, ["Alice Smith"])
    html = adm.test_client().get("/albums/a").text
    assert html.index("albums/a/k0/") < html.index("Alice Smith") < html.index("albums/a/k2/")


def test_the_jump_starts_the_album_at_that_photo_with_its_group_heading_and_a_way_back(adm, client):
    make_album(adm, "a", 6)
    sign_in(client)
    start_group(client, "a", 1, ["Alice Smith", "Bob Jones"])
    start_group(client, "a", 5, ["Cara Lee", "Dan Wu"])
    pub = adm.test_client()
    start = pub.get("/albums/a").text
    assert "Back to the start" not in start and start.count('class="tile"') == 6
    jumped = pub.get("/albums/a?at=3").text  # photo 3 is mid-group
    assert jumped.count('class="tile"') == 4 and "albums/a/k0/" not in jumped and "albums/a/k1/" not in jumped
    assert "Alice Smith" in jumped  # the group's heading still shows, so you know whose photos these are
    assert 'href="/albums/a">&larr; Back to the start' in jumped and "back-fab" in jumped
    assert pub.get("/albums/a?at=999").text.count('class="tile"') == 6  # unknown photo: just the start
    assert pub.get("/albums/a?at=abc").status_code == 200


def test_paging_continues_after_a_jump_and_keeps_group_headings_right(adm, client):
    adm.config["GALLERY_PAGE_SIZE"] = 2
    make_album(adm, "a", 8)
    sign_in(client)
    start_group(client, "a", 1, ["Alice Smith"])
    start_group(client, "a", 4, ["Cara Lee"])
    pub = adm.test_client()
    first = pub.get("/albums/a?at=2").text
    assert 'hx-get="/albums/a/photos?offset=3"' in first  # photo 2 is position 1; the next page starts after 2 photos
    frag = pub.get("/albums/a/photos?offset=3").text  # photos 4,5: a new group starts at 4 (a page boundary)
    assert "Cara Lee" in frag and frag.index('class="grid-break"') < frag.index("Cara Lee")
    mid = pub.get("/albums/a/photos?offset=4").text  # photos 5,6: same group as before, no new heading
    assert "group-head" not in mid and "grid-break" not in mid


def test_the_home_page_grid_is_unaffected(adm, client):
    make_album(adm, "home", 3)
    html = adm.test_client().get("/").text
    assert html.count('class="tile"') == 3 and "grid-break" not in html and "group-head" not in html


def test_the_index_is_the_highlights_in_album_order_with_dancer_names(adm, client):
    make_album(adm, "a", 8)
    sign_in(client)
    start_group(client, "a", 1, ["Alice Smith", "Bob Jones"])
    start_group(client, "a", 5, ["Cara Lee", "Dan Wu"])
    post(client, "/admin/albums/a/highlights", {"add": [7, 2, 5]})  # added out of order
    html = adm.test_client().get("/albums/a").text
    links = re.findall(r'class="index-card" href="([^"]+)"', html)
    assert links == ["/albums/a?at=2#grid", "/albums/a?at=5#grid", "/albums/a?at=7#grid"]
    captions = re.findall(r'class="index-names">([^<]*)<', html)
    assert captions == ["Alice Smith &amp; Bob Jones", "Cara Lee &amp; Dan Wu", "Cara Lee &amp; Dan Wu"]
    assert html.index("album-index") < html.index('id="grid"')


def test_no_highlights_means_no_index(adm, client):
    make_album(adm, "a", 3)
    assert "album-index" not in adm.test_client().get("/albums/a").text


def test_deleting_a_groups_first_photo_moves_the_marker_on(adm, client):
    make_album(adm, "a", 6)
    sign_in(client)
    g1 = start_group(client, "a", 2, ["Alice Smith"])
    start_group(client, "a", 5, ["Cara Lee"])
    post(client, "/admin/photos/delete", {"ids": [2]})
    assert [tuple(r) for r in query(adm, "SELECT start_photo_id FROM photo_groups WHERE id = ?", g1)] == [(3,)]  # next photo that stays
    post(client, "/admin/photos/delete", {"ids": [3, 4]})  # the rest of group 1 goes; group 2 starts right after
    assert query(adm, "SELECT COUNT(*) FROM photo_groups WHERE id = ?", g1)[0][0] == 0  # empty: dropped
    assert query(adm, "SELECT COUNT(*) FROM photo_groups")[0][0] == 1
    assert "Alice Smith" in [r[0] for r in query(adm, "SELECT name FROM tags")]  # the person is still known


def test_deleting_a_whole_album_tail_drops_its_group(adm, client):
    make_album(adm, "a", 3)
    sign_in(client)
    start_group(client, "a", 3, ["Alice Smith"])
    post(client, "/admin/photos/delete", {"ids": [3]})
    assert query(adm, "SELECT COUNT(*) FROM photo_groups")[0][0] == 0


def test_group_removal_keeps_the_photos(adm, client):
    make_album(adm, "a", 3)
    sign_in(client)
    gid = start_group(client, "a", 2, ["Alice Smith"])
    assert post(client, f"/admin/groups/{gid}/delete", {}).status_code == 200
    assert query(adm, "SELECT COUNT(*) FROM photos")[0][0] == 3 and names(adm.test_client().get("/albums/a").text) == []


def test_admin_page_has_the_name_suggestions_and_the_group_controls(adm, client):
    make_album(adm, "a", 2)
    sign_in(client)
    start_group(client, "a", 1, ["Alice Smith"])
    html = client.get("/albums/a").text
    assert '<datalist id="people-list">' in html and 'value="Alice Smith"' in html
    assert 'id="sel-group"' in html and "data-people='[&#34;Alice Smith&#34;]'" in html.replace("'[\"Alice Smith\"]'", "'[&#34;Alice Smith&#34;]'")
    assert json.loads(re.search(r"data-people='([^']*)'", html).group(1).replace("&#34;", '"')) == ["Alice Smith"]
