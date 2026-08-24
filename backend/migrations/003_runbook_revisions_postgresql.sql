-- Acrescenta linhagem append-only: cada edição será outro Job e outro artefato.
-- A versão publicada anterior nunca é atualizada nem removida.
BEGIN;

LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE;

ALTER TABLE jobs
    ADD COLUMN root_job_id VARCHAR(36) NULL,
    ADD COLUMN supersedes_job_id VARCHAR(36) NULL,
    ADD COLUMN revision_number INTEGER NOT NULL DEFAULT 1;

ALTER TABLE jobs
    ADD CONSTRAINT fk_jobs_root_job
        FOREIGN KEY (root_job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_jobs_supersedes_job
        FOREIGN KEY (supersedes_job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
    ADD CONSTRAINT uq_jobs_supersedes_job_id UNIQUE (supersedes_job_id),
    ADD CONSTRAINT uq_jobs_root_revision UNIQUE (root_job_id, revision_number),
    ADD CONSTRAINT ck_jobs_revision_lineage CHECK (
        (
            root_job_id IS NULL
            AND supersedes_job_id IS NULL
            AND revision_number = 1
        )
        OR
        (
            root_job_id IS NOT NULL
            AND supersedes_job_id IS NOT NULL
            AND revision_number >= 2
        )
    );

CREATE INDEX ix_jobs_root_job_id ON jobs (root_job_id);

COMMIT;
