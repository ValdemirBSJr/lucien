-- Areas adicionais por usuario. A area primaria continua em
-- users.domain_function: e o destino quando `lucien start` roda sem `-r`.
-- Vazio preserva o comportamento anterior, entao nenhum usuario existente
-- ganha alcance novo com esta migracao.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS extra_domains JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_extra_domains_shape;

-- Precisa ser um array de textos: o Hub deriva autorizacao dele, e um objeto
-- ou um escalar aqui viraria erro so na hora de publicar.
ALTER TABLE users
    ADD CONSTRAINT users_extra_domains_shape
    CHECK (jsonb_typeof(extra_domains) = 'array');
