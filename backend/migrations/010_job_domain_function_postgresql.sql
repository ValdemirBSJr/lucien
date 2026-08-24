-- Guarda o dominio escolhido em `lucien start -r`.
-- NULL preserva o comportamento anterior: publica no dominio do autor.
-- Jobs existentes ficam NULL e continuam publicando como antes.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS domain_function TEXT;

ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_domain_function_format;

-- A mesma gramatica de users.domain_function: o valor vira nome de diretorio
-- no repositorio publicado.
ALTER TABLE jobs
    ADD CONSTRAINT jobs_domain_function_format
    CHECK (domain_function IS NULL OR domain_function ~ '^[a-z][a-z0-9_]{2,63}$');
