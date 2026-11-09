-- Imported village exports. The raw JSON is the source of truth: the state is parsed from it on
-- read, so that a parser fix applies to the exports already stored.
CREATE TABLE villages (
    village_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE village_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    village_id TEXT NOT NULL REFERENCES villages (village_id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    format_version TEXT NOT NULL,
    exported_at INTEGER NOT NULL,
    imported_at INTEGER NOT NULL,
    UNIQUE (village_id, sha256)
);
