-- Espelho em banco de tudo que foi publicado, imagens inclusive.
--
-- Ate aqui o Hub guardava apenas o hash do conteudo: o documento e os anexos
-- viviam so no destino (volume local, GitHub ou Gitea). Isso amarra o acervo
-- ao provedor -- sair do Gitea para uma wiki local exigiria migrar o
-- repositorio, e o Hub nao saberia reconstruir nada sozinho.
--
-- Com o espelho, a arvore publicada e reproduzivel a partir do banco. O
-- caminho gravado e o relativo a raiz dos documentos, SEM o prefixo do
-- provedor Git, porque e ele que vale igual em local, github e gitea.
--
-- Uma revisao que herda uma imagem sem altera-la nao a reenvia: os bytes
-- ficam sob o job do ancestral. Nao ha duplicacao de proposito -- a exportacao
-- e da arvore inteira, e a linha do ancestral fornece o arquivo, exatamente
-- como o Git o fornece hoje.
BEGIN;

CREATE TABLE IF NOT EXISTS published_documents (
    job_id VARCHAR(36) PRIMARY KEY REFERENCES jobs(id) ON DELETE RESTRICT,
    markdown TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    document_sha256 VARCHAR(64) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- BYTEA e nao base64: base64 inflaria 33% e cobraria uma decodificacao a cada
-- leitura, sem nada em troca. O PostgreSQL ja comprime e desloca valor grande
-- para TOAST por conta propria.
CREATE TABLE IF NOT EXISTS published_assets (
    job_id VARCHAR(36) NOT NULL
        REFERENCES published_documents(job_id) ON DELETE CASCADE,
    filename VARCHAR(128) NOT NULL,
    relative_path TEXT NOT NULL,
    content BYTEA NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    PRIMARY KEY (job_id, filename)
);

-- Apagar um usuario nao pode apagar o que ele documentou.
--
-- jobs.owner_id nascia CASCADE, entao remover a linha de um usuario levava
-- junto todos os runbooks publicados por ele -- e, agora, o espelho deles. Um
-- runbook publicado e conhecimento da equipe, nao propriedade de quem o
-- escreveu: desligar alguem nao pode desfazer os procedimentos que a equipe
-- passou a seguir.
--
-- O caminho normal de saida continua sendo `is_active = false`, que preserva
-- tudo. A partir daqui, quem insistir em apagar a linha do usuario recebe uma
-- recusa do PostgreSQL em vez de um apagamento silencioso.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_owner_id_fkey;
ALTER TABLE jobs ADD CONSTRAINT jobs_owner_id_fkey
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE RESTRICT;

COMMIT;
