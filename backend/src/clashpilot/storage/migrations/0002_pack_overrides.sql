-- Manual corrections of pack rows. The stored pack content is never rewritten: corrections are
-- applied when a pack is read. A NULL field keeps the value of the pack.
CREATE TABLE pack_overrides (
    pack_id TEXT NOT NULL REFERENCES gamedata_packs (pack_id) ON DELETE CASCADE,
    data_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    duration_s INTEGER,
    cost TEXT,
    note TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (pack_id, data_id, level)
);
