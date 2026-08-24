BEGIN;

CREATE TABLE IF NOT EXISTS service_credentials (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    scope VARCHAR(64) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_service_credentials_name_scope UNIQUE (name, scope)
);

CREATE INDEX IF NOT EXISTS ix_service_credentials_scope
    ON service_credentials (scope);
CREATE INDEX IF NOT EXISTS ix_service_credentials_token_hash
    ON service_credentials (token_hash);

COMMIT;
