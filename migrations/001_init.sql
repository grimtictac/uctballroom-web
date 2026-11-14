CREATE TABLE users (
    id          INTEGER PRIMARY KEY,
    google_sub  TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL,
    name        TEXT,
    avatar_url  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE albums (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    description   TEXT,
    event_date    TEXT,
    cover_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE photos (
    id          INTEGER PRIMARY KEY,
    album_id    INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    base_key    TEXT NOT NULL UNIQUE,   -- Space prefix, e.g. albums/<slug>/<id>
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    caption     TEXT,
    alt         TEXT,
    credit      TEXT,
    taken_at    TEXT,                   -- from EXIF, ISO 8601
    sort_order  INTEGER,                -- manual override; NULL = order by taken_at
    placeholder TEXT,                   -- dominant colour / blur-up data
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_photos_album ON photos(album_id, sort_order, taken_at);

CREATE TABLE tags (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    slug     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('topic', 'person')),
    user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- person tag linked to an account
    UNIQUE (kind, slug)
);

CREATE TABLE photo_tags (
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    added_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (photo_id, tag_id)
);

CREATE TABLE favourites (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, photo_id)
);

CREATE TABLE ratings (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    stars      INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, photo_id)
);

CREATE TABLE collections (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    is_shared   INTEGER NOT NULL DEFAULT 0,   -- 1 = viewable by link
    share_token TEXT UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE collection_photos (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (collection_id, photo_id)
);
