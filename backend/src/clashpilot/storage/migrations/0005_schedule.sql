-- The player's schedule: one replaceable document per village. No foreign key to villages: a
-- schedule can be typed in before the first import.
CREATE TABLE schedule_profile (
    village_id TEXT PRIMARY KEY,
    body_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
