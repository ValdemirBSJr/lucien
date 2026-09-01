import hashlib
from pathlib import Path

import pytest

from app.application import JobService
from app.domain.models import RoleLevel, SecurityContext
from app.domain.ports import (
    ImageSecurityScanner,
    ProcessedAsset,
    RawAssetInput,
    SecretDetectedError,
    SecretScanner,
    SecretScanResult,
    ValidationError,
)
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.storage import LocalProvider


class _Scanner(SecretScanner):
    def __init__(self) -> None:
        self.scanned: list[str] = []

    async def detect(self, content: str) -> SecretScanResult:
        self.scanned.append(content)
        return SecretScanResult(detected=False)


class _FakeImageScanner(ImageSecurityScanner):
    """Aprova qualquer imagem e devolve bytes canonicos, sem OCR de verdade."""

    def __init__(self, reject_with: Exception | None = None) -> None:
        self.calls: list[bytes] = []
        self._reject_with = reject_with

    async def process(self, raw_bytes: bytes, declared_media_type: str) -> ProcessedAsset:
        self.calls.append(raw_bytes)
        if self._reject_with is not None:
            raise self._reject_with
        return ProcessedAsset(content=raw_bytes, media_type="image/png")


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'assets.db').as_posix()}"
    instance = SQLAlchemyJobRepository(database_url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _context(user) -> SecurityContext:
    return SecurityContext.from_user(user)


async def _user(repository: SQLAlchemyJobRepository, username: str, domain: str = "servidores"):
    token_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return await repository.create_user(username, token_hash, RoleLevel.SENIOR, domain)


def _service(
    repository: SQLAlchemyJobRepository,
    storage: LocalProvider,
    image_scanner: ImageSecurityScanner,
    max_assets_per_publication: int = 20,
) -> JobService:
    return JobService(
        repository,
        _Scanner(),
        storage,
        image_scanner,
        revisions_enabled=True,
        max_assets_per_publication=max_assets_per_publication,
    )


def _asset(filename: str = "shot.png") -> RawAssetInput:
    return RawAssetInput(
        filename=filename, content_base64="ZmFrZS1wbmc=", media_type="image/png"
    )


async def test_publish_with_valid_asset_writes_file_and_rewrites_reference(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    playbooks = tmp_path / "playbooks"
    image_scanner = _FakeImageScanner()
    service = _service(repository, LocalProvider(playbooks), image_scanner)
    user = await _user(repository, "autor")
    job = await repository.create_job(user.id, "job-com-imagem", ("echo ok",), ())

    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print da tela](assets/{job.id}/shot.png)\n"
    )
    published, _ = await service.publish(
        _context(user), job.id, markdown, "publish-asset-0001", (_asset(),)
    )

    md_path = next(playbooks.rglob(f"*--{published.id}.md"))
    stored_markdown = md_path.read_text()
    assert f"assets/{job.id}/shot.png" not in stored_markdown
    assert f"assets/{job.id}/" in stored_markdown

    asset_dir = md_path.parent / "assets" / job.id
    written = list(asset_dir.iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == b"fake-png"
    assert len(image_scanner.calls) == 1


async def test_orphan_reference_never_reaches_reservation(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks), _FakeImageScanner())
    user = await _user(repository, "autor-orfao")
    job = await repository.create_job(user.id, "job-orfao", ("echo ok",), ())

    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print sem asset](assets/{job.id}/nao-enviado.png)\n"
    )
    with pytest.raises(ValidationError, match="not submitted"):
        await service.publish(
            _context(user), job.id, markdown, "publish-orphan-0001", (_asset(),)
        )

    # A reserva nunca aconteceu: o job continua PENDING.
    reloaded = await repository.get_job(user.id, job.id)
    assert reloaded.status.value == "PENDING"


async def test_secret_detected_in_image_is_rejected(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    playbooks = tmp_path / "playbooks"
    image_scanner = _FakeImageScanner(
        reject_with=SecretDetectedError(
            "content blocked by the secret policy (rule: lucien-snmp-community)"
        )
    )
    service = _service(repository, LocalProvider(playbooks), image_scanner)
    user = await _user(repository, "autor-segredo")
    job = await repository.create_job(user.id, "job-segredo", ("echo ok",), ())

    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print com segredo](assets/{job.id}/shot.png)\n"
    )
    with pytest.raises(SecretDetectedError, match="lucien-snmp-community"):
        await service.publish(
            _context(user), job.id, markdown, "publish-secret-0001", (_asset(),)
        )
    assert not list(playbooks.rglob("*.md"))


async def test_too_many_assets_is_rejected(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(
        repository, LocalProvider(playbooks), _FakeImageScanner(), max_assets_per_publication=1
    )
    user = await _user(repository, "autor-limite")
    job = await repository.create_job(user.id, "job-limite", ("echo ok",), ())

    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![um](assets/{job.id}/a.png)\n![dois](assets/{job.id}/b.png)\n"
    )
    with pytest.raises(ValidationError, match="at most 1"):
        await service.publish(
            _context(user),
            job.id,
            markdown,
            "publish-limit-0001",
            (_asset("a.png"), _asset("b.png")),
        )


async def test_idempotent_retry_returns_same_published_job(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks), _FakeImageScanner())
    user = await _user(repository, "autor-retry")
    job = await repository.create_job(user.id, "job-retry", ("echo ok",), ())
    markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print](assets/{job.id}/shot.png)\n"
    )

    first, _ = await service.publish(
        _context(user), job.id, markdown, "publish-retry-0001", (_asset(),)
    )
    second, _ = await service.publish(
        _context(user), job.id, markdown, "publish-retry-0001", (_asset(),)
    )

    assert first.id == second.id
    assert first.content_hash == second.content_hash
    assert len(list(playbooks.rglob(f"*--{first.id}.md"))) == 1


async def test_revise_with_new_asset_uses_revision_job_id_in_stored_path(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks), _FakeImageScanner())
    user = await _user(repository, "autor-revisao")
    job = await repository.create_job(user.id, "job-revisar", ("echo ok",), ())
    base_markdown = "### Step 1: Run\n```bash\necho ok\n```\n"
    published, _ = await service.publish(
        _context(user), job.id, base_markdown, "publish-base-0001"
    )

    # O autor referencia o job de ORIGEM -- o id da revisao ainda nao existe.
    revision_markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print novo](assets/{published.id}/shot.png)\n"
    )
    revised, _ = await service.revise(
        _context(user),
        published.id,
        published.content_hash,
        revision_markdown,
        "revise-asset-0001",
        (_asset(),),
    )

    md_path = next(playbooks.rglob(f"*--{revised.id}.md"))
    stored_markdown = md_path.read_text()
    assert f"assets/{revised.id}/" in stored_markdown
    assert (md_path.parent / "assets" / revised.id).is_dir()


async def test_revise_keeps_inherited_image_as_text_without_resubmitting_it(
    tmp_path: Path,
    repository: SQLAlchemyJobRepository,
) -> None:
    """Uma imagem ja publicada continua so como texto -- revisar so pra
    adicionar uma segunda imagem nao deveria exigir reenviar a primeira."""
    playbooks = tmp_path / "playbooks"
    image_scanner = _FakeImageScanner()
    service = _service(repository, LocalProvider(playbooks), image_scanner)
    user = await _user(repository, "autor-heranca")
    job = await repository.create_job(user.id, "job-com-imagem-herdada", ("echo ok",), ())

    base_markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"![print original](assets/{job.id}/shot.png)\n"
    )
    published, _ = await service.publish(
        _context(user), job.id, base_markdown, "publish-heranca-0001", (_asset(),)
    )
    published_md_path = next(playbooks.rglob(f"*--{published.id}.md"))
    inherited_reference = next(
        line for line in published_md_path.read_text().splitlines() if line.startswith("![")
    )

    # A referencia herdada (com o nome opaco atribuido no publish) e mantida
    # tal como esta; so a segunda imagem e nova e precisa vir em `assets`.
    revision_markdown = (
        "### Step 1: Run\n```bash\necho ok\n```\n"
        f"{inherited_reference}\n"
        f"![print novo](assets/{published.id}/new.png)\n"
    )
    revised, _ = await service.revise(
        _context(user),
        published.id,
        published.content_hash,
        revision_markdown,
        "revise-heranca-0001",
        (_asset("new.png"),),
    )

    revised_md_path = next(playbooks.rglob(f"*--{revised.id}.md"))
    stored_markdown = revised_md_path.read_text()
    # A referencia herdada nao muda de lugar nem de nome -- continua apontando
    # para o job de origem, onde o arquivo fisico sempre esteve.
    assert inherited_reference in stored_markdown
    assert f"assets/{revised.id}/" in stored_markdown
    assert (revised_md_path.parent / "assets" / revised.id).is_dir()
    # So a imagem nova foi reprocessada -- a herdada nao precisou de OCR de novo.
    assert len(image_scanner.calls) == 2
