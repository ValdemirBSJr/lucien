BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS command_outputs JSON NOT NULL DEFAULT '[]'::json,
    ADD COLUMN IF NOT EXISTS runbook_suggestions JSON NOT NULL DEFAULT '{}'::json;

COMMIT;
