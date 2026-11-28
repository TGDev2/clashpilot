-- The village a plan was computed for (E32): "home" (the main village) or "builder" (the builder
-- base). The plans stored before this migration were all computed for the main village.
ALTER TABLE plans ADD COLUMN base TEXT NOT NULL DEFAULT 'home';

CREATE INDEX plans_by_village_base ON plans (village_id, base, plan_id);
