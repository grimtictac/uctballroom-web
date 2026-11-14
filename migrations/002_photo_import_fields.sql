ALTER TABLE photos ADD COLUMN original_ext TEXT NOT NULL DEFAULT 'jpg';
ALTER TABLE photos ADD COLUMN variants TEXT NOT NULL DEFAULT '400,800,1600';  -- widths that exist
ALTER TABLE photos ADD COLUMN source_sha256 TEXT;                              -- dedupe re-imports
CREATE UNIQUE INDEX idx_photos_album_sha ON photos(album_id, source_sha256)
    WHERE source_sha256 IS NOT NULL;
