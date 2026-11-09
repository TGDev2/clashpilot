-- What the export does not give: typed-in values (one replaceable document per village) and the
-- levels read from the official API, which are shown over the export without touching it.
CREATE TABLE village_manual (
    village_id TEXT PRIMARY KEY REFERENCES villages (village_id) ON DELETE CASCADE,
    body_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE village_api_levels (
    village_id TEXT NOT NULL REFERENCES villages (village_id) ON DELETE CASCADE,
    data_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (village_id, data_id)
);
