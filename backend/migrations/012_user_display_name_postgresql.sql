-- Nome completo entregue pelo LDAP, para o frontmatter do runbook publicado.
-- NULL preserva o comportamento anterior: o artefato mostra o username.
-- O username continua sendo a identidade autoritativa; este campo e exibicao.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS display_name TEXT;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_display_name_length;

ALTER TABLE users
    ADD CONSTRAINT users_display_name_length
    CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 120);
