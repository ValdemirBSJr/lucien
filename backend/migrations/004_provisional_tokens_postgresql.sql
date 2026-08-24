-- Adiciona ativação temporária de uso único sem invalidar tokens legados.
-- Execute após backup verificado e antes de publicar a API/CLI com este contrato.
BEGIN;

LOCK TABLE users IN ACCESS EXCLUSIVE MODE;

ALTER TABLE users
    ALTER COLUMN api_token_hash DROP NOT NULL,
    ADD COLUMN provisional_token_hash VARCHAR(64) NULL,
    ADD COLUMN provisional_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN provisional_exchange_key_hash VARCHAR(64) NULL;

ALTER TABLE users
    ADD CONSTRAINT uq_users_provisional_token_hash
        UNIQUE (provisional_token_hash),
    ADD CONSTRAINT ck_users_provisional_pair
        CHECK (
            (provisional_token_hash IS NULL AND provisional_expires_at IS NULL)
            OR
            (provisional_token_hash IS NOT NULL AND provisional_expires_at IS NOT NULL)
        ),
    ADD CONSTRAINT ck_users_provisional_exchange_key
        CHECK (
            provisional_token_hash IS NOT NULL
            OR provisional_exchange_key_hash IS NULL
        );

COMMIT;
