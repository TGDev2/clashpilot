-- What-if scenarios of a village (E18). A scenario is computed on a copy of the problem of the
-- latest plan and never touches the real tables: `overlays` is the JSON of the request overlays,
-- `body` the scenario as the API answers it, with the compared figures under `figures`.
CREATE TABLE scenarios (
    scenario_id INTEGER PRIMARY KEY AUTOINCREMENT,
    village_id TEXT NOT NULL REFERENCES villages (village_id) ON DELETE CASCADE,
    plan_id INTEGER REFERENCES plans (plan_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    overlays TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX scenarios_by_village ON scenarios (village_id, scenario_id);
