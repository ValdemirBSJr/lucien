"""Migrações versionadas contra um PostgreSQL de verdade.

Migração é o código que só roda uma vez, na hora em que o banco de produção
está na mesa. SQLite não serve para prová-lo: nem os tipos nem o catálogo são
os mesmos, e é justamente o catálogo que sustenta os marcadores.

O portão `migrations` de `scripts/verify.sh` sobe um PostgreSQL descartável e
roda este arquivo. Sem `POSTGRES_TEST_DATABASE_URL` os testes são pulados.
"""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.migrations import MIGRACOES

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="PostgreSQL de integração não configurado",
)

# Desfaz o efeito das doze migrações sobre o esquema que o modelo cria, para
# obter o banco anterior a todas elas. Derivar o ancestral do modelo em vez de
# escrevê-lo à mão evita que ele envelheça enquanto o resto do código anda.
DESFAZER = """
ALTER TABLE users DROP COLUMN provisional_scope CASCADE;
DROP TABLE IF EXISTS user_credentials CASCADE;
ALTER TABLE users DROP COLUMN display_name CASCADE;
ALTER TABLE users DROP COLUMN extra_domains CASCADE;
ALTER TABLE jobs DROP COLUMN domain_function CASCADE;
DROP TABLE IF EXISTS upload_queue CASCADE;
ALTER TABLE jobs DROP COLUMN description CASCADE;
ALTER TABLE jobs DROP COLUMN command_outputs CASCADE;
ALTER TABLE jobs DROP COLUMN runbook_suggestions CASCADE;
DROP TABLE IF EXISTS service_credentials CASCADE;
ALTER TABLE jobs DROP COLUMN root_job_id CASCADE;
ALTER TABLE jobs DROP COLUMN supersedes_job_id CASCADE;
ALTER TABLE jobs DROP COLUMN revision_number CASCADE;
ALTER TABLE users DROP COLUMN provisional_token_hash CASCADE;
ALTER TABLE users DROP COLUMN provisional_expires_at CASCADE;
ALTER TABLE users DROP COLUMN provisional_exchange_key_hash CASCADE;
DROP TABLE IF EXISTS bootstrap_state CASCADE;
ALTER TABLE users DROP COLUMN role_level CASCADE;
ALTER TABLE users DROP COLUMN domain_function CASCADE;
ALTER TABLE users DROP COLUMN is_active CASCADE;
ALTER TABLE users RENAME COLUMN username TO name;
ALTER TABLE users RENAME COLUMN api_token_hash TO api_key_digest;
ALTER TABLE jobs DROP COLUMN inferred_tags CASCADE;
ALTER TABLE jobs DROP COLUMN publication_identity CASCADE;
ALTER TABLE jobs DROP COLUMN upload_fingerprint CASCADE;
ALTER TABLE jobs DROP COLUMN processing_error CASCADE;
DROP TABLE IF EXISTS schema_migrations CASCADE;
"""


@asynccontextmanager
async def _bruta(url: str):
    """Conexão asyncpg crua, com o engine descartado ao fim."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conexao:
            yield (await conexao.get_raw_connection()).driver_connection
    finally:
        await engine.dispose()


@pytest.fixture
async def banco():
    """Um banco vazio por teste, dentro do PostgreSQL de integração."""
    base_url = os.environ["POSTGRES_TEST_DATABASE_URL"]
    nome = f"prova_{uuid4().hex[:12]}"
    administrativo = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        async with administrativo.connect() as conexao:
            bruta = (await conexao.get_raw_connection()).driver_connection
            await bruta.execute(f'CREATE DATABASE "{nome}"')
    finally:
        await administrativo.dispose()

    url = base_url.rsplit("/", 1)[0] + "/" + nome
    try:
        yield url
    finally:
        administrativo = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
        try:
            async with administrativo.connect() as conexao:
                bruta = (await conexao.get_raw_connection()).driver_connection
                await bruta.execute(
                    f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'
                )
        finally:
            await administrativo.dispose()


async def _estado(url: str) -> dict[str, str]:
    async with _bruta(url) as bruta:
        linhas = await bruta.fetch(
            "SELECT versao, origem FROM schema_migrations ORDER BY versao"
        )
    return {linha["versao"]: linha["origem"] for linha in linhas}


async def _executar(url: str, script: str) -> None:
    async with _bruta(url) as bruta:
        await bruta.execute(script)


async def _subir(url: str) -> None:
    repositorio = SQLAlchemyJobRepository(url)
    try:
        await repositorio.initialize()
    finally:
        await repositorio.close()


async def test_instalacao_nova_nasce_com_a_lista_quitada(banco: str) -> None:
    """O modelo cria o esquema inteiro; as migrações não têm o que fazer."""
    await _subir(banco)

    estado = await _estado(banco)
    assert set(estado) == {migracao.versao for migracao in MIGRACOES}
    assert set(estado.values()) == {"modelo"}


async def test_banco_anterior_a_todas_as_migracoes_alcanca_o_nivel_atual(
    banco: str,
) -> None:
    await _subir(banco)
    await _executar(banco, DESFAZER)

    await _subir(banco)

    estado = await _estado(banco)
    assert set(estado) == {migracao.versao for migracao in MIGRACOES}
    # Nenhuma foi adotada: todas precisaram rodar de fato.
    assert set(estado.values()) == {"aplicada"}


async def test_banco_migrado_a_mao_e_adotado_sem_reaplicar(banco: str) -> None:
    """A instalação que já existe não pode ver a 001 rodar de novo.

    Aqui o esquema está completo e o registro não existe -- exatamente o estado
    de quem aplicou os arquivos `.sql` na mão antes deste módulo.
    """
    await _subir(banco)
    await _executar(banco, "DROP TABLE schema_migrations")

    await _subir(banco)

    estado = await _estado(banco)
    assert set(estado) == {migracao.versao for migracao in MIGRACOES}
    assert set(estado.values()) == {"adotada"}


async def test_queda_entre_aplicar_e_registrar_se_conserta_sozinha(
    banco: str,
) -> None:
    """O marcador é a verdade; o registro é só o caminho rápido."""
    await _subir(banco)
    await _executar(
        banco, "DELETE FROM schema_migrations WHERE versao >= '010'"
    )

    await _subir(banco)

    estado = await _estado(banco)
    assert set(estado) == {migracao.versao for migracao in MIGRACOES}
    assert estado["012"] == "adotada"
    assert estado["001"] == "modelo"


async def test_subida_repetida_nao_toca_no_esquema(banco: str) -> None:
    await _subir(banco)
    antes = await _estado(banco)

    await _subir(banco)
    await _subir(banco)

    assert await _estado(banco) == antes


async def test_banco_migrado_continua_operavel(banco: str) -> None:
    """Migrar não basta: o esquema resultante precisa servir ao Hub."""
    from app.domain.models import RoleLevel

    await _subir(banco)
    await _executar(banco, DESFAZER)
    await _subir(banco)

    repositorio = SQLAlchemyJobRepository(banco)
    try:
        usuario = await repositorio.create_user(
            "migrado", "h" * 64, RoleLevel.ADMIN, "plataforma"
        )
        job = await repositorio.create_job(usuario.id, "runbook", ("ls",), ("linux",))
        assert (await repositorio.get_job(usuario.id, job.id)).name == "runbook"
    finally:
        await repositorio.close()
