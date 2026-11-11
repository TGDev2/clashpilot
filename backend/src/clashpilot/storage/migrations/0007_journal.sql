-- The journal of each village (E11): one row per event recorded through
-- POST /api/villages/{village_id}/plans/{plan_id}/events, never for a dry run. `payload` is the JSON
-- of every value the replay reads; `plan_id` is the plan the event produced.
CREATE TABLE journal (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    village_id TEXT NOT NULL REFERENCES villages (village_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    at INTEGER NOT NULL,
    task_id TEXT,
    payload TEXT NOT NULL,
    plan_id INTEGER REFERENCES plans (plan_id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX journal_by_village ON journal (village_id, at, entry_id);

-- A replanned plan names the plan it replaces, and every plan the export it was computed on, so
-- that a newer import marks it stale. Both stay null on the plans stored before this migration.
ALTER TABLE plans ADD COLUMN previous_plan_id INTEGER;
ALTER TABLE plans ADD COLUMN snapshot_id INTEGER;
