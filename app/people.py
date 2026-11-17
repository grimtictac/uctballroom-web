"""People named on photo groups. They are 'person' rows in `tags`, so they can be reused for tagging and search."""
import hashlib
import re
import unicodedata

MAX_PEOPLE = 6
MAX_NAME = 80


def clean_name(raw):
    return re.sub(r"\s+", " ", raw).strip()


def slugify(name):
    """Lowercase ASCII slug ('Zoë  Smith' -> 'zoe-smith'); names with no ASCII letters get a stable hash."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "person-" + hashlib.sha1(name.lower().encode()).hexdigest()[:8]


def get_or_create_person(conn, name):
    """The person's tag id. The same name in any case or spacing is the same person."""
    slug = slugify(name)
    row = conn.execute("SELECT id FROM tags WHERE kind = 'person' AND slug = ?", (slug,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO tags (name, slug, kind) VALUES (?, ?, 'person')", (name, slug)).lastrowid


def set_group_people(conn, group_id, names):
    """Replace the dancers on a group, keeping the order given. Does not commit."""
    ids = []
    for name in names:
        tag_id = get_or_create_person(conn, name)
        if tag_id not in ids:
            ids.append(tag_id)
    conn.execute("DELETE FROM group_people WHERE group_id = ?", (group_id,))
    for position, tag_id in enumerate(ids, 1):
        conn.execute("INSERT INTO group_people (group_id, tag_id, position) VALUES (?, ?, ?)",
                     (group_id, tag_id, position))


def all_people(conn):
    return [r[0] for r in conn.execute("SELECT name FROM tags WHERE kind = 'person' ORDER BY name COLLATE NOCASE")]
