-- Migração única para instalações criadas antes do IAM/RBAC.
-- Execute em janela controlada e com backup verificado do PostgreSQL.
BEGIN;

LOCK TABLE users, jobs IN ACCESS EXCLUSIVE MODE;

ALTER TABLE users RENAME COLUMN name TO username;
ALTER TABLE users RENAME COLUMN api_key_digest TO api_token_hash;
ALTER TABLE users
    ADD COLUMN role_level VARCHAR(16) NOT NULL DEFAULT 'junior',
    ADD COLUMN domain_function VARCHAR(64) NOT NULL DEFAULT 'servidores',
    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE users
    ADD CONSTRAINT ck_users_role_level
        CHECK (role_level IN ('junior', 'pleno', 'senior', 'admin')),
    ADD CONSTRAINT ck_users_domain_function
        CHECK (domain_function ~ '^[a-z][a-z0-9_]{2,63}$');

CREATE INDEX ix_users_is_active ON users (is_active);

ALTER TABLE jobs
    ADD COLUMN inferred_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN publication_identity JSONB NULL;

-- Usuários legados permanecem como junior por menor privilégio. Como não haverá
-- admin ativo, o bootstrap controlado poderá emitir o primeiro admin da nova fase.
COMMIT;
