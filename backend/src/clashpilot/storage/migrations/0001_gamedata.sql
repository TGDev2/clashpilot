-- Game data packs: one row per imported pack, the full content kept as canonical JSON.
CREATE TABLE gamedata_packs (
    pack_id TEXT PRIMARY KEY,
    game_version TEXT NOT NULL,
    as_of TEXT NOT NULL,
    fictional INTEGER NOT NULL CHECK (fictional IN (0, 1)),
    items INTEGER NOT NULL,
    levels INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1))
);

-- At most one active pack at any time.
CREATE UNIQUE INDEX gamedata_packs_single_active ON gamedata_packs (active) WHERE active = 1;
