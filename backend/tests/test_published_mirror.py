"""O espelho em banco tem que reproduzir a árvore publicada sem o Git.

O critério destes testes não é "gravou alguma coisa no banco", e sim: exportar
o espelho para um diretório vazio produz byte a byte o que o provedor escreveu.
Qualquer coisa menos que isso deixaria a wiki local dependendo do repositório,
que é justamente o que o espelho existe para evitar.
"""

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from app.application import JobService
from app.domain.models import RoleLevel, SecurityContext
from app.domain.ports import (
    ImageSecurityScanner,
    ProcessedAsset,
    RawAssetInput,
    SecretScanner,
    SecretScanResult,
    StorageProvider,
    UpstreamError,
)
from app.export_wiki import escrever_documento
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.storage import LocalProvider, MirroredStorage


class _Scanner(SecretScanner):
    async def detect(self, content: str) -> SecretScanResult:
        return SecretScanResult(detected=False)


class _FakeImageScanner(ImageSecurityScanner):
    async def process(
        self, raw_bytes: bytes, declared_media_type: str
    ) -> ProcessedAsset:
        return ProcessedAsset(content=raw_bytes, media_type="image/png")


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'mirror.db').as_posix()}"
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


def _arvore(raiz: Path) -> dict[str, bytes]:
    """Toda a árvore como {caminho relativo: bytes}, para comparar direto."""

    return {
        caminho.relative_to(raiz).as_posix(): caminho.read_bytes()
        for caminho in sorted(raiz.rglob("*"))
        if caminho.is_file()
    }


async def _exportar(
    repository: SQLAlchemyJobRepository, destino: Path
) -> dict[str, bytes]:
    """Exporta e extrai, como o operador faria: `... > wiki.tar && tar -xf`.

    Passar pelo tar de verdade, e não só pela função que o alimenta, é o que
    prova que o arquivo entregue ao host contém a árvore inteira.
    """

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w|") as arquivo:
        async for documento in repository.iter_published_mirror():
            escrever_documento(arquivo, documento)
    destino.mkdir(parents=True, exist_ok=True)
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|") as arquivo:
        arquivo.extractall(destino, filter="data")
    return _arvore(destino)


async def test_arvore_exportada_do_banco_e_identica_a_publicada(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    playbooks = tmp_path / "playbooks"
    storage = MirroredStorage(LocalProvider(playbooks), repository)
    service = _service(repository, storage)
    user = await _user(repository, "autor-espelho")
    job = await repository.create_job(user.id, "runbook-com-imagem", ("echo ok",), ())

    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print da tela](assets/{job.id}/shot.png)\n"
    )
    await service.publish(
        _context(user), job.id, markdown, "publish-espelho-0001", (_asset(),)
    )

    exportado = await _exportar(repository, tmp_path / "wiki")

    # A prova: o que saiu do banco é o que o provedor escreveu, mesmos
    # caminhos e mesmos bytes -- markdown e imagem.
    assert exportado == _arvore(playbooks)
    assert any(caminho.endswith(".png") for caminho in exportado)
    assert any(caminho.endswith(".md") for caminho in exportado)


async def test_revisao_espelha_a_nova_versao_e_preserva_a_anterior(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    playbooks = tmp_path / "playbooks"
    storage = MirroredStorage(LocalProvider(playbooks), repository)
    service = _service(repository, storage)
    user = await _user(repository, "autor-revisao")
    job = await repository.create_job(user.id, "runbook-revisado", ("echo ok",), ())

    publicado, _ = await service.publish(
        _context(user),
        job.id,
        "### Step 1: Run\n```bash\necho ok\n```\n",
        "publish-revisao-0001",
    )
    assert publicado.content_hash is not None
    revisao, _ = await service.revise(
        _context(user),
        publicado.id,
        publicado.content_hash,
        "### Step 1: Run revised\n```bash\necho revised\n```\n",
        "revise-espelho-0001",
    )

    exportado = await _exportar(repository, tmp_path / "wiki")

    # As duas versões estão lá: o artefato é imutável, e o espelho não pode
    # apagar a anterior só porque uma sucessora foi publicada.
    assert exportado == _arvore(playbooks)
    assert sum(1 for caminho in exportado if caminho.endswith(".md")) == 2
    assert any(publicado.id in caminho for caminho in exportado)
    assert any(revisao.id in caminho for caminho in exportado)


async def test_imagem_herdada_por_revisao_continua_exportavel(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """A revisão não reenvia a imagem; ela vive sob o job do ancestral.

    O espelho não a duplica de propósito. Como a exportação é da árvore
    inteira, a linha do ancestral fornece o arquivo -- exatamente como o Git o
    fornece hoje. Se este teste falhar, a wiki local nasce com link quebrado.
    """

    playbooks = tmp_path / "playbooks"
    storage = MirroredStorage(LocalProvider(playbooks), repository)
    service = _service(repository, storage)
    user = await _user(repository, "autor-heranca")
    job = await repository.create_job(user.id, "runbook-herdado", ("echo ok",), ())

    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print da tela](assets/{job.id}/shot.png)\n"
    )
    publicado, _ = await service.publish(
        _context(user), job.id, markdown, "publish-heranca-0001", (_asset(),)
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
        "revise-heranca-0001",
    )

    exportado = await _exportar(repository, tmp_path / "wiki")

    assert exportado == _arvore(playbooks)
    # A referência herdada aponta para um arquivo que a exportação gravou.
    (revisada,) = [
        conteudo.decode("utf-8")
        for caminho, conteudo in exportado.items()
        if caminho.endswith(".md") and "runbook_raiz:" in conteudo.decode("utf-8")
    ]
    referencia = next(
        linha for linha in revisada.splitlines() if linha.startswith("![")
    )
    caminho_relativo = referencia.split("(", 1)[1].rstrip(")")
    md_relativo = next(
        caminho
        for caminho, conteudo in exportado.items()
        if conteudo.decode("utf-8", errors="ignore") == revisada
    )
    alvo = (Path(md_relativo).parent / caminho_relativo).as_posix()
    assert alvo in exportado


async def test_falha_do_espelho_impede_marcar_o_job_como_publicado(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """Espelhar é parte de publicar, não um efeito colateral tolerável.

    O artefato já está gravado quando o espelho falha -- e é assim mesmo: a
    publicação é idempotente, então a repetição reencontra o arquivo e
    completa o espelho. O que não pode acontecer é o job virar PUBLISHED com
    o banco vazio, porque aí nada mais tentaria de novo.
    """

    class _EspelhoQueFalha:
        async def save_published(self, document) -> None:
            raise UpstreamError("banco indisponível")

    playbooks = tmp_path / "playbooks"
    storage = MirroredStorage(LocalProvider(playbooks), _EspelhoQueFalha())
    service = _service(repository, storage)
    user = await _user(repository, "autor-falha")
    job = await repository.create_job(user.id, "runbook-falho", ("echo ok",), ())

    with pytest.raises(UpstreamError):
        await service.publish(
            _context(user),
            job.id,
            "### Step 1: Run\n```bash\necho ok\n```\n",
            "publish-falha-0001",
        )

    recarregado = await repository.get_job(user.id, job.id)
    assert recarregado.status.value != "PUBLISHED"
