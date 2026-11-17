-- Several highlight photos per album (the card cycles through them), and site-wide admin settings.
CREATE TABLE album_highlights (
    album_id  INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    photo_id  INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    x         REAL NOT NULL DEFAULT 50,   -- where this photo's crop sits, % across
    y         REAL NOT NULL DEFAULT 35,   -- % down
    PRIMARY KEY (album_id, photo_id)
);
CREATE INDEX idx_album_highlights_order ON album_highlights(album_id, position);

CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
