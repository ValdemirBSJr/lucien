"""O backfill tem que produzir o mesmo estado que uma publicação de hoje.

O critério é esse, e não "gravou linhas no banco": publica-se sem espelho,
roda-se o backfill, e o resultado tem que ser indistinguível de ter publicado
com o espelho ligado desde o começo. Se divergir, o acervo antigo entra na
wiki local diferente do novo, e a diferença só apareceria na migração.
"""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.application import JobService
from app.backfill_mirror import espelhar_publicacao
from app.domain.models import RoleLevel, SecurityContext
from app.domain.ports import (
    ImageSecurityScanner,
    NotFoundError,
    ProcessedAsset,
    RawAssetInput,
    SecretScanner,
    SecretScanResult,
    StorageProvider,
)
from app.infrastructure.database import (
    PublishedAssetRow,
    PublishedDocumentRow,
    SQLAlchemyJobRepository,
)
from app.infrastructure.storage import LocalProvider, MirroredStorage


class _Scanner(SecretScanner):
    async def detect(self, content: str) -> SecretScanResult:
        return SecretScanResult(detected=False)


class _FakeImageScanner(ImageSecurityScanner):
    async def process(
        self, raw_bytes: bytes, declared_media_type: str
    ) -> ProcessedAsset:
        return ProcessedAsset(content=raw_bytes, media_type="image/png")


def _url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'backfill.db').as_posix()}"


async def _limpar_espelho(database_url: str) -> None:
    """Esvazia só as tabelas do espelho, deixando os jobs publicados.

    É o estado exato de uma instalação que publicou antes de o espelho
    existir, e a única forma de compará-lo com o resultado ao vivo sem
    republicar (o que geraria ids novos).
    """

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conexao:
            await conexao.execute(delete(PublishedAssetRow))
            await conexao.execute(delete(PublishedDocumentRow))
    finally:
        await engine.dispose()


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = _url(tmp_path)
    instance = SQLAlchemyJobRepository(database_url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _context(user) -> SecurityContext:
    return SecurityContext.from_user(user)


async def _user(
    repository: SQLAlchemyJobRepository, username: str, domain: str = "servidores"
):
    token_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return await repository.create_user(
        username, token_hash, RoleLevel.SENIOR, domain
    )


def _service(
    repository: SQLAlchemyJobRepository, storage: StorageProvider
) -> JobService:
    return JobService(
        repository,
        _Scanner(),
        storage,
        _FakeImageScanner(),
        revisions_enabled=True,
    )


def _asset(filename: str = "shot.png") -> RawAssetInput:
    return RawAssetInput(
        filename=filename, content_base64="ZmFrZS1wbmc=", media_type="image/png"
    )


async def _espelho_completo(
    repository: SQLAlchemyJobRepository,
) -> dict[str, tuple[str, str, tuple[tuple[str, str, bytes], ...]]]:
    """O espelho inteiro, comparável entre duas execuções diferentes."""

    conteudo = {}
    async for documento in repository.iter_published_mirror():
        conteudo[documento.job_id] = (
            documento.markdown,
            documento.relative_path,
            tuple(
                (anexo.filename, anexo.relative_path, anexo.content)
                for anexo in documento.assets
            ),
        )
    return conteudo


async def _publicar_acervo(
    repository: SQLAlchemyJobRepository, storage: StorageProvider
) -> None:
    """Um runbook com imagem, e uma revisão dele que herda essa imagem.

    A revisão é o caso que separa um backfill correto de um plausível: ela
    referencia um anexo que nunca lhe pertenceu.
    """

    service = _service(repository, storage)
    user = await _user(repository, "autor-acervo")
    job = await repository.create_job(user.id, "runbook-antigo", ("echo ok",), ())
    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print da tela](assets/{job.id}/shot.png)\n"
    )
    publicado, _ = await service.publish(
        _context(user), job.id, markdown, "publish-acervo-0001", (_asset(),)
    )
    assert publicado.content_hash is not None
    corpo, content_hash = await service.published_content(
        _context(user), publicado.id
    )
    await service.revise(
        _context(user),
        publicado.id,
        content_hash,
        corpo + "\n> Observacao acrescentada na revisao.\n",
        "revise-acervo-0001",
    )


async def _rodar_backfill(
    repository: SQLAlchemyJobRepository, storage: StorageProvider
) -> int:
    pendentes = await repository.published_ids_without_mirror()
    for job_id in pendentes:
        await espelhar_publicacao(repository, storage, job_id)
    return len(pendentes)


async def test_backfill_reproduz_o_que_o_espelho_ao_vivo_teria_gravado(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """Publica com espelho, apaga o espelho, reconstrói pelo backfill.

    Publicar duas vezes em bancos separados e comparar não serviria: cada
    publicação nasce com UUID novo, e o id e a data entram no frontmatter. Ao
    apagar e reconstruir sobre o MESMO acervo, a comparação passa a ser exata
    -- markdown, caminho e bytes de cada anexo.
    """

    playbooks = tmp_path / "playbooks"
    provider = LocalProvider(playbooks)
    await _publicar_acervo(repository, MirroredStorage(provider, repository))

    ao_vivo = await _espelho_completo(repository)
    assert len(ao_vivo) == 2

    await _limpar_espelho(_url(tmp_path))
    assert await _espelho_completo(repository) == {}

    assert await _rodar_backfill(repository, provider) == 2
    assert await _espelho_completo(repository) == ao_vivo


async def test_backfill_nao_duplica_a_imagem_herdada_pela_revisao(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """A revisão referencia a imagem do ancestral, mas não a possui.

    Copiar os bytes para a linha dela pareceria inofensivo e inflaria o banco
    a cada revisão de um runbook ilustrado -- e faria a exportação gravar o
    mesmo arquivo duas vezes, em caminhos diferentes.
    """

    playbooks = tmp_path / "playbooks"
    provider = LocalProvider(playbooks)
    await _publicar_acervo(repository, provider)
    await _rodar_backfill(repository, provider)

    espelho = await _espelho_completo(repository)
    com_anexo = [
        job_id for job_id, (_, _, anexos) in espelho.items() if anexos
    ]
    assert len(com_anexo) == 1

    # E a que tem o anexo é a publicação original, não a revisão.
    (markdown, _, anexos) = espelho[com_anexo[0]]
    assert "runbook_raiz:" not in markdown
    assert anexos[0][2] == b"fake-png"


async def test_backfill_e_seguro_de_repetir(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    playbooks = tmp_path / "playbooks"
    provider = LocalProvider(playbooks)
    await _publicar_acervo(repository, provider)

    assert await _rodar_backfill(repository, provider) == 2
    primeira = await _espelho_completo(repository)
    # Nada pendente na segunda vez: o que já está espelhado sai da lista.
    assert await _rodar_backfill(repository, provider) == 0
    assert await _espelho_completo(repository) == primeira


async def test_artefato_ausente_no_repositorio_falha_alto(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """O `main` pula e relata; a função em si precisa acusar.

    Engolir aqui faria o backfill gravar uma linha vazia e reportar sucesso --
    o pior desfecho possível, porque só apareceria na migração.
    """

    playbooks = tmp_path / "playbooks"
    provider = LocalProvider(playbooks)
    await _publicar_acervo(repository, provider)
    (pendente, _) = await repository.published_ids_without_mirror()

    for artefato in playbooks.rglob("*.md"):
        artefato.unlink()

    with pytest.raises(NotFoundError):
        await espelhar_publicacao(repository, provider, pendente)
