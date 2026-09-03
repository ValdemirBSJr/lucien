"""Migrações versionadas do PostgreSQL, aplicadas na subida do Hub.

Até aqui o esquema evoluía por doze arquivos `.sql` aplicados à mão, na ordem
de uma lista na documentação, sem nenhum registro do que já tinha rodado. Quem
migrava precisava lembrar onde parou, e não havia como perguntar ao banco.

Duas coisas sustentam este módulo:

**O registro** (`schema_migrations`) é o caminho rápido: diz o que já foi
aplicado sem tocar no esquema.

**O marcador** é a verdade. Cada migração declara uma pergunta ao catálogo do
PostgreSQL que só responde sim depois que ela rodou -- a coluna que ela cria, a
tabela que ela acrescenta. É o marcador que permite adotar uma instalação já
migrada à mão sem reaplicar nada, e é ele que conserta a queda entre executar a
migração e registrá-la: na subida seguinte o marcador responde sim e a versão é
quitada em vez de repetida.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_log = logging.getLogger(__name__)

DIRETORIO = Path(__file__).resolve().parents[2] / "migrations"

# Trava de sessão para que duas réplicas subindo juntas não apliquem a mesma
# migração em paralelo. O valor é fixo e derivado do nome do projeto.
_CHAVE_TRAVA = int.from_bytes(b"lucien", "big")


async def _asyncpg(conexao: AsyncConnection) -> Any:
    """A conexão do asyncpg por baixo da do SQLAlchemy.

    As migrações são scripts com várias instruções, que o asyncpg só executa
    pelo protocolo simples -- fora do alcance da camada do SQLAlchemy.
    """
    bruta = (await conexao.get_raw_connection()).driver_connection
    if bruta is None:
        raise RuntimeError("conexão asyncpg indisponível")
    return bruta


def _coluna(tabela: str, coluna: str) -> str:
    return (
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = '{tabela}' "
        f"AND column_name = '{coluna}')"
    )


def _tabela(nome: str) -> str:
    return f"SELECT to_regclass('public.{nome}') IS NOT NULL"


def _regra_de_exclusao(restricao: str, regra: str) -> str:
    """Marcador para migração cujo efeito é trocar o ON DELETE de uma FK."""

    return (
        "SELECT EXISTS (SELECT 1 FROM information_schema.referential_constraints "
        f"WHERE constraint_schema = 'public' AND constraint_name = '{restricao}' "
        f"AND delete_rule = '{regra}')"
    )


@dataclass(frozen=True)
class Migracao:
    versao: str
    arquivo: str
    marcador: str

    @property
    def caminho(self) -> Path:
        return DIRETORIO / self.arquivo


# A ordem é a de aplicação e não pode ser reorganizada: 001 pressupõe o esquema
# anterior ao IAM, e cada uma seguinte pressupõe as anteriores.
MIGRACOES: tuple[Migracao, ...] = (
    Migracao("001", "001_iam_rbac_postgresql.sql", _coluna("users", "role_level")),
    Migracao("002", "002_bootstrap_state_postgresql.sql", _tabela("bootstrap_state")),
    Migracao(
        "003", "003_runbook_revisions_postgresql.sql", _coluna("jobs", "root_job_id")
    ),
    Migracao(
        "004",
        "004_provisional_tokens_postgresql.sql",
        _coluna("users", "provisional_token_hash"),
    ),
    Migracao("005", "005_async_upload_queue_postgresql.sql", _tabela("upload_queue")),
    Migracao(
        "006",
        "006_jump_enrollment_credentials_postgresql.sql",
        _tabela("service_credentials"),
    ),
    Migracao(
        "007", "007_command_outputs_postgresql.sql", _coluna("jobs", "command_outputs")
    ),
    Migracao(
        "008", "008_job_description_postgresql.sql", _coluna("jobs", "description")
    ),
    Migracao(
        "009",
        "009_skip_enrichment_postgresql.sql",
        _coluna("upload_queue", "skip_enrichment"),
    ),
    Migracao(
        "010",
        "010_job_domain_function_postgresql.sql",
        _coluna("jobs", "domain_function"),
    ),
    Migracao(
        "011", "011_user_extra_domains_postgresql.sql", _coluna("users", "extra_domains")
    ),
    Migracao(
        "012", "012_user_display_name_postgresql.sql", _coluna("users", "display_name")
    ),
    Migracao(
        "013",
        "013_user_scoped_credentials_postgresql.sql",
        # A coluna e o ultimo efeito do script, depois da tabela e dos indices
        # -- um banco onde so a tabela exista (adocao parcial, ou uma corrida
        # anterior interrompida) ainda precisa rodar o script de novo. CREATE
        # TABLE IF NOT EXISTS torna isso seguro de repetir.
        _coluna("users", "provisional_scope"),
    ),
    Migracao(
        "014",
        "014_published_mirror_postgresql.sql",
        # A troca da FK, e não uma das tabelas novas: é o último efeito do
        # script, e o único que uma adoção à mão poderia ter deixado para trás
        # depois de criar as tabelas. O nome serve aos dois caminhos de
        # criação -- o PostgreSQL nomeia `<tabela>_<coluna>_fkey` para a FK
        # que o modelo cria, e o script reusa esse mesmo nome.
        _regra_de_exclusao("jobs_owner_id_fkey", "RESTRICT"),
    ),
)

_CRIAR_REGISTRO = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    versao TEXT PRIMARY KEY,
    aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    origem TEXT NOT NULL
)
"""


def corpo_executavel(texto: str) -> str:
    """Devolve o script sem o BEGIN/COMMIT do arquivo.

    Os arquivos continuam aplicáveis à mão com `psql -f`, e ali o BEGIN é o que
    garante atomicidade. Aqui ele atrapalha: quem controla a transação é o
    runner, que precisa incluir nela também o registro da versão.
    """
    linhas = [
        linha
        for linha in texto.splitlines()
        if linha.strip().upper() not in {"BEGIN;", "COMMIT;"}
    ]
    return "\n".join(linhas)


async def aplicar_migracoes(engine: AsyncEngine) -> list[str]:
    """Leva um banco existente ao nível do código. Devolve as versões tocadas."""
    async with engine.connect() as conexao:
        bruta = await _asyncpg(conexao)
        await bruta.execute("SELECT pg_advisory_lock($1)", _CHAVE_TRAVA)
        try:
            if not await bruta.fetchval(_tabela("users")):
                # Instalação nova: não há esquema anterior para migrar. O modelo
                # cria as tabelas e `registrar_esquema_do_modelo` quita a lista.
                return []
            await bruta.execute(_CRIAR_REGISTRO)
            registradas = {
                linha["versao"]
                for linha in await bruta.fetch("SELECT versao FROM schema_migrations")
            }
            tocadas: list[str] = []
            for migracao in MIGRACOES:
                if migracao.versao in registradas:
                    continue
                if await bruta.fetchval(migracao.marcador):
                    # Já está no banco: aplicada à mão antes deste módulo existir,
                    # ou aplicada por nós numa subida que caiu antes de registrar.
                    async with bruta.transaction():
                        await _registrar(bruta, migracao.versao, "adotada")
                    _log.info("migração %s adotada", migracao.versao)
                    tocadas.append(migracao.versao)
                    continue
                script = corpo_executavel(
                    migracao.caminho.read_text(encoding="utf-8")
                )
                async with bruta.transaction():
                    await bruta.execute(script)
                    await _registrar(bruta, migracao.versao, "aplicada")
                _log.info("migração %s aplicada", migracao.versao)
                tocadas.append(migracao.versao)
            return tocadas
        finally:
            await bruta.execute("SELECT pg_advisory_unlock($1)", _CHAVE_TRAVA)


async def registrar_esquema_do_modelo(engine: AsyncEngine) -> list[str]:
    """Quita as versões cujo efeito o modelo já pôs no banco.

    Numa instalação nova o `create_all` monta o esquema completo de uma vez;
    sem isto, a primeira subida seguinte tentaria aplicar a 001 sobre um banco
    que já a contém.
    """
    async with engine.connect() as conexao:
        bruta = await _asyncpg(conexao)
        await bruta.execute("SELECT pg_advisory_lock($1)", _CHAVE_TRAVA)
        try:
            await bruta.execute(_CRIAR_REGISTRO)
            registradas = {
                linha["versao"]
                for linha in await bruta.fetch("SELECT versao FROM schema_migrations")
            }
            quitadas: list[str] = []
            for migracao in MIGRACOES:
                if migracao.versao in registradas:
                    continue
                if not await bruta.fetchval(migracao.marcador):
                    continue
                async with bruta.transaction():
                    await _registrar(bruta, migracao.versao, "modelo")
                quitadas.append(migracao.versao)
            if quitadas:
                _log.info("esquema criado pelo modelo: %s", ", ".join(quitadas))
            return quitadas
        finally:
            await bruta.execute("SELECT pg_advisory_unlock($1)", _CHAVE_TRAVA)


async def _registrar(bruta: Any, versao: str, origem: str) -> None:
    await bruta.execute(
        "INSERT INTO schema_migrations (versao, origem) VALUES ($1, $2) "
        "ON CONFLICT (versao) DO NOTHING",
        versao,
        origem,
    )
