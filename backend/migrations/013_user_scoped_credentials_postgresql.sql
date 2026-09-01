-- Credencial permanente por escopo, por usuario.
--
-- Ate aqui, UserRow tinha uma unica coluna de token permanente
-- (api_token_hash): toda troca de provisorio a sobrescrevia, sem excecao.
-- Isso e exatamente o problema para uma identidade gerida pelo jump server --
-- o hook de login SSH reemite e troca um provisorio a cada acesso, e essa
-- troca apagava qualquer token permanente que a pessoa estivesse usando fora
-- do jump (por exemplo, no app desktop).
--
-- user_credentials guarda credenciais permanentes adicionais, isoladas por
-- escopo. O escopo ausente (NULL em provisional_scope, ou seja, o
-- comportamento de hoje) continua gravando em users.api_token_hash -- ninguem
-- que nao pediu escopo tem o comportamento alterado.
BEGIN;

CREATE TABLE IF NOT EXISTS user_credentials (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope VARCHAR(64) NOT NULL,
    api_token_hash VARCHAR(64) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_credentials_user_scope UNIQUE (user_id, scope)
);

CREATE INDEX IF NOT EXISTS ix_user_credentials_token_hash
    ON user_credentials (api_token_hash);
CREATE INDEX IF NOT EXISTS ix_user_credentials_user_id
    ON user_credentials (user_id);

-- Registra para qual escopo a proxima troca de provisorio deve gravar.
-- NULL preserva o fluxo de hoje (grava em users.api_token_hash).
ALTER TABLE users ADD COLUMN IF NOT EXISTS provisional_scope VARCHAR(64) NULL;

COMMIT;
