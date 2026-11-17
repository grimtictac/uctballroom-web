-- Admin controls: album order, cover framing, and a record of removed photos.
ALTER TABLE albums ADD COLUMN sort_order INTEGER;                 -- manual order; NULL = after the ordered ones, by title
ALTER TABLE albums ADD COLUMN cover_x REAL NOT NULL DEFAULT 50;   -- where the card crop sits, % across the photo
ALTER TABLE albums ADD COLUMN cover_y REAL NOT NULL DEFAULT 35;   -- % down the photo (heads are usually high)
ALTER TABLE albums ADD COLUMN cover_fit TEXT NOT NULL DEFAULT 'crop' CHECK (cover_fit IN ('crop', 'full'));

-- Fingerprints of photos deleted by an admin, so re-running an import never brings them back.
CREATE TABLE removed_photos (
    album_id       INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    source_sha256  TEXT NOT NULL,
    removed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (album_id, source_sha256)
);
