BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS upload_fingerprint VARCHAR(64),
    ADD COLUMN IF NOT EXISTS processing_error VARCHAR(64);

CREATE TABLE IF NOT EXISTS upload_queue (
    job_id VARCHAR(36) PRIMARY KEY
        REFERENCES jobs(id) ON DELETE CASCADE,
    ciphertext TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL,
    lease_until TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_upload_queue_available_at
    ON upload_queue (available_at);

CREATE INDEX IF NOT EXISTS ix_upload_queue_lease_until
    ON upload_queue (lease_until);

COMMIT;
