-- The stock history of each village (E17): one row per resource of each PUT .../manual, never
-- replaced. GET .../farm-rate reads it to observe the farm rate between two inputs.
CREATE TABLE manual_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    village_id TEXT NOT NULL REFERENCES villages (village_id) ON DELETE CASCADE,
    resource TEXT NOT NULL,
    value INTEGER NOT NULL,
    as_of INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL
);

CREATE INDEX manual_history_by_village ON manual_history (village_id, resource, as_of, history_id);
