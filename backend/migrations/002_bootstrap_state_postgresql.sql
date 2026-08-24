-- Fecha o bootstrap do primeiro administrador com um latch transacional.
-- Execute após 001_iam_rbac_postgresql.sql e antes de escalar o Hub.
BEGIN;

CREATE TABLE IF NOT EXISTS bootstrap_state (
    id VARCHAR(32) PRIMARY KEY,
    completed BOOLEAN NOT NULL
);

INSERT INTO bootstrap_state (id, completed)
SELECT
    'initial-admin',
    EXISTS (SELECT 1 FROM users WHERE role_level = 'admin')
ON CONFLICT (id) DO NOTHING;

COMMIT;
