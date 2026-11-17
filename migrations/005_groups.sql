-- Groups within an album: a marker on a photo starts a new group, which runs to the next marker.
-- Everyone named on a group counts as appearing in all of its photos (people are 'person' rows in tags).
CREATE TABLE photo_groups (
    id              INTEGER PRIMARY KEY,
    album_id        INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    start_photo_id  INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    UNIQUE (album_id, start_photo_id)
);

CREATE TABLE group_people (
    group_id  INTEGER NOT NULL REFERENCES photo_groups(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    PRIMARY KEY (group_id, tag_id)
);
