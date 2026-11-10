-- Plans computed for a village. Only plans the simulator found valid are stored (ADR-4).
-- `params` is the JSON of the resolved request parameters (horizon_days, engine, profile), read
-- back by a replanning; `body` is the plan as the API answers it, `explanations` and
-- `reliability` what the recommendations of this plan say.
CREATE TABLE plans (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    village_id TEXT NOT NULL REFERENCES villages (village_id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    now INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    engine TEXT NOT NULL,
    profile TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    proof TEXT NOT NULL,
    assignments TEXT NOT NULL,
    trace TEXT NOT NULL,
    warnings TEXT NOT NULL,
    params TEXT NOT NULL,
    body TEXT NOT NULL,
    explanations TEXT NOT NULL,
    reliability TEXT NOT NULL
);

CREATE INDEX plans_by_village ON plans (village_id, plan_id);
