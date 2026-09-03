import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError as PydanticValidationError

from app.api.routes import _parse_if_match
from app.api.schemas import RevisionRequest
from app.application import JobService
from app.domain.models import (
    PublicationIdentity,
    RoleLevel,
    SecurityContext,
)
from app.domain.ports import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
    SecretDetectedError,
    SecretScanner,
    SecretScanResult,
    StorageProvider,
    UpstreamError,
    ValidationError,
)
from app.domain.publication import validate_playbook
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.storage import LocalProvider


class _Scanner(SecretScanner):
    def __init__(self) -> None:
        self.blocked: set[str] = set()
        self.scanned: list[str] = []

    async def detect(self, content: str) -> SecretScanResult:
        self.scanned.append(content)
        return SecretScanResult(detected=content in self.blocked)


class _FlakyStorage(StorageProvider):
    def __init__(self, delegate: StorageProvider, failures: int = 1) -> None:
        self._delegate = delegate
        self._failures = failures

    async def publish(
        self,
        job_id,
        created_at,
        markdown,
        artifact_name=None,
        domain_function=None,
    ):
        if self._failures:
            self._failures -= 1
            raise UpstreamError("storage temporariamente indisponível")
        return await self._delegate.publish(
            job_id,
            created_at,
            markdown,
            artifact_name=artifact_name,
            domain_function=domain_function,
        )

    async def read_published(
        self,
        job_id,
        created_at,
        artifact_name=None,
        domain_function=None,
    ) -> str:
        return await self._delegate.read_published(
            job_id,
            created_at,
            artifact_name=artifact_name,
            domain_function=domain_function,
        )


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'revisions.db').as_posix()}"
    instance = SQLAlchemyJobRepository(database_url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _context(user) -> SecurityContext:
    return SecurityContext.from_user(user)


def _iso(momento: datetime) -> str:
    """Mesma serialização do frontmatter, para comparar sem reescrevê-la."""

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def _user(
    repository: SQLAlchemyJobRepository,
    username: str,
    role: RoleLevel,
    domain: str,
):
    token_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return await repository.create_user(username, token_hash, role, domain)


def _service(
    repository: SQLAlchemyJobRepository,
    storage: StorageProvider,
    scanner: _Scanner | None = None,
    revisions_enabled: bool = True,
) -> JobService:
    return JobService(
        repository,
        scanner or _Scanner(),
        storage,
        revisions_enabled=revisions_enabled,
    )


def _markdown(action: str, command: str, explanation: str = "") -> str:
    suffix = f"> {explanation}\n" if explanation else ""
    return f"### Step 1: {action}\n```bash\n{command}\n```\n{suffix}"


async def _publish_base(
    repository: SQLAlchemyJobRepository,
    service: JobService,
    author,
    name: str,
):
    job = await repository.create_job(
        author.id, name, ("echo original",), ("shell",)
    )
    published, _ = await service.publish(
        _context(author),
        job.id,
        _markdown("Run original command", "echo original"),
        f"publish-{name}-0001",
    )
    assert published.content_hash is not None
    return published


async def test_rbac_dominio_frontmatter_e_imutabilidade(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    author = await _user(repository, "source-author", RoleLevel.SENIOR, "servidores")
    junior = await _user(repository, "junior-editor", RoleLevel.JUNIOR, "servidores")
    pleno = await _user(repository, "pleno-editor", RoleLevel.PLENO, "servidores")
    other_domain = await _user(
        repository, "network-senior", RoleLevel.SENIOR, "redes"
    )
    senior = await _user(repository, "server-senior", RoleLevel.SENIOR, "servidores")
    admin = await _user(repository, "global-admin", RoleLevel.ADMIN, "plataforma")
    platform_senior = await _user(
        repository, "platform-senior", RoleLevel.SENIOR, "plataforma"
    )
    source = await _publish_base(repository, service, author, "rbac-source")
    original_path = next(playbooks.rglob(f"*--{source.id}.md"))
    original_bytes = original_path.read_bytes()
    revision_markdown = _markdown(
        "Run revised command", "echo revised", "Validate the revised output."
    )

    for denied in (junior, pleno):
        with pytest.raises(ForbiddenError):
            await service.revise(
                _context(denied),
                source.id,
                source.content_hash,
                revision_markdown,
                f"denied-{denied.username}",
            )

    # Um senior de outro domínio não deve distinguir ausência de falta de escopo.
    with pytest.raises(NotFoundError):
        await service.revise(
            _context(other_domain),
            source.id,
            source.content_hash,
            revision_markdown,
            "denied-network-senior",
        )

    captured_events: list[tuple[str, str, dict[str, str]]] = []

    def capture_event(event: str, actor_id: str, **fields: str) -> None:
        captured_events.append((event, actor_id, fields))

    monkeypatch.setattr("app.application.audit_event", capture_event)
    revision, replacements = await service.revise(
        _context(senior),
        source.id,
        source.content_hash,
        revision_markdown,
        "revision-rbac-0001",
    )

    assert replacements == 0
    assert revision.root_job_id == source.id
    assert revision.supersedes_job_id == source.id
    assert revision.revision_number == 2
    assert revision.owner_id == senior.id
    assert original_path.read_bytes() == original_bytes
    revision_path = next(playbooks.rglob(f"*--{revision.id}.md"))
    revision_text = revision_path.read_text(encoding="utf-8")
    assert f'runbook_raiz: "{source.id}"' in revision_text
    assert "revisao: 2" in revision_text
    assert f'substitui: "{source.id}"' in revision_text
    # A autoria é a da raiz: `source-author` escreveu o runbook e continua
    # sendo quem o escreveu depois que `server-senior` o corrigiu.
    assert 'autor: "source-author"' in revision_text
    assert 'ultimo_revisor: "server-senior"' in revision_text
    assert f'data_criacao: "{_iso(source.created_at)}"' in revision_text
    assert f'data_revisao: "{_iso(revision.created_at)}"' in revision_text
    assert "runbook_raiz:" not in original_bytes.decode("utf-8")
    assert captured_events[0][0] == "runbook.revise"
    assert captured_events[0][1] == senior.id
    assert "markdown" not in captured_events[0][2]
    serialized_audit_fields = repr(captured_events[0][2])
    assert "echo revised" not in serialized_audit_fields
    assert "Validate the revised output" not in serialized_audit_fields

    second_source = await _publish_base(repository, service, author, "admin-source")
    admin_revision, _ = await service.revise(
        _context(admin),
        second_source.id,
        second_source.content_hash,
        revision_markdown,
        "revision-admin-0001",
    )
    assert admin_revision.owner_id == admin.id
    admin_revision_text = next(
        playbooks.rglob(f"*--{admin_revision.id}.md")
    ).read_text(encoding="utf-8")
    # O admin é de `plataforma` e revisou um runbook de `servidores`. O
    # frontmatter declarava a área DELE enquanto o arquivo era gravado em
    # `<ano>/servidores/` -- o documento contradizia a própria pasta, e o
    # portal precisava subir a cadeia até a raiz para achar o domínio real.
    assert 'autor: "source-author"' in admin_revision_text
    assert 'funcao: "servidores"' in admin_revision_text
    assert 'ultimo_revisor: "global-admin"' in admin_revision_text

    # O editor aparece no frontmatter como revisor, e nem isso lhe dá o
    # domínio: quem pode revisar continua sendo decidido pela raiz.
    with pytest.raises(NotFoundError):
        await service.revise(
            _context(platform_senior),
            admin_revision.id,
            admin_revision.content_hash,
            _markdown("Platform edit denied", "echo denied"),
            "revision-platform-denied",
        )
    third_revision, _ = await service.revise(
        _context(senior),
        admin_revision.id,
        admin_revision.content_hash,
        _markdown("Server edit allowed", "echo allowed"),
        "revision-server-allowed",
    )
    assert third_revision.revision_number == 3
    third_revision_text = next(
        playbooks.rglob(f"*--{third_revision.id}.md")
    ).read_text(encoding="utf-8")
    # Terceira versão, terceiro revisor: a procedência não se desloca nem
    # encadeia -- continua sendo a da raiz, não a da versão anterior.
    assert 'autor: "source-author"' in third_revision_text
    assert 'funcao: "servidores"' in third_revision_text
    assert 'ultimo_revisor: "server-senior"' in third_revision_text
    assert f'data_criacao: "{_iso(second_source.created_at)}"' in third_revision_text


async def test_spoof_scanner_e_dlp_continuam_obrigatorios(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    scanner = _Scanner()
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks), scanner)
    author = await _user(repository, "dlp-author", RoleLevel.SENIOR, "servidores")
    editor = await _user(repository, "dlp-editor", RoleLevel.SENIOR, "servidores")
    source = await _publish_base(repository, service, author, "dlp-source")

    forged = (
        "---\nautor: admin\nnivel_autor: admin\n---\n"
        + _markdown("Spoof identity", "echo forged")
    )
    with pytest.raises(ValidationError):
        await service.revise(
            _context(editor),
            source.id,
            source.content_hash,
            forged,
            "revision-spoof-0001",
        )

    blocked = _markdown("Expose blocked marker", "echo BLOQUEADO")
    scanner.blocked.add(blocked)
    with pytest.raises(SecretDetectedError):
        await service.revise(
            _context(editor),
            source.id,
            source.content_hash,
            blocked,
            "revision-blocked-0001",
        )

    with_secret = _markdown(
        "Configure Redis", "REDIS_PASSWORD=segredo-final redis-cli ping"
    )
    revision, replacements = await service.revise(
        _context(editor),
        source.id,
        source.content_hash,
        with_secret,
        "revision-dlp-0001",
    )
    assert replacements >= 1
    revision_text = next(playbooks.rglob(f"*--{revision.id}.md")).read_text(
        encoding="utf-8"
    )
    assert "segredo-final" not in revision_text
    assert "SUA_SENHA_REDIS_AQUI" in revision_text
    assert blocked in scanner.scanned


async def test_falha_storage_permite_reconciliacao_sem_trocar_autoria(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    playbooks = tmp_path / "playbooks"
    stable_service = _service(repository, LocalProvider(playbooks))
    author = await _user(repository, "retry-author", RoleLevel.SENIOR, "servidores")
    editor = await _user(repository, "retry-editor", RoleLevel.SENIOR, "servidores")
    admin = await _user(repository, "retry-admin", RoleLevel.ADMIN, "plataforma")
    source = await _publish_base(repository, stable_service, author, "retry-source")
    flaky_service = _service(
        repository, _FlakyStorage(LocalProvider(playbooks), failures=1)
    )
    markdown = _markdown("Retry safely", "echo retry")
    key = "revision-retry-0001"

    with pytest.raises(UpstreamError):
        await flaky_service.revise(
            _context(editor), source.id, source.content_hash, markdown, key
        )

    validated = validate_playbook(markdown)
    revision_hash = hashlib.sha256(validated.body.encode("utf-8")).hexdigest()
    pending = await repository.reserve_revision(
        editor.id,
        source.id,
        source.content_hash,
        revision_hash,
        key,
        PublicationIdentity.from_context(_context(editor)),
        validated.command_blocks,
        datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    assert pending.status.value == "PENDING"
    assert await repository.list_pending(editor.id) == []
    with pytest.raises(NotFoundError):
        await repository.get_job(editor.id, pending.id)
    with pytest.raises(NotFoundError):
        await repository.delete_job(editor.id, pending.id)
    with pytest.raises(NotFoundError):
        await flaky_service.publish(_context(editor), pending.id, markdown, key)

    with pytest.raises(ConflictError):
        await flaky_service.revise(
            _context(editor),
            source.id,
            source.content_hash,
            _markdown("Different attempt", "echo different"),
            "revision-retry-0002",
        )

    await repository.revoke_user(editor.id)
    published, _ = await flaky_service.revise(
        _context(admin),
        source.id,
        source.content_hash,
        markdown,
        "revision-recovery-by-admin",
    )
    retried, _ = await flaky_service.revise(
        _context(admin),
        source.id,
        source.content_hash,
        markdown,
        "revision-recovery-by-admin",
    )
    assert published.id == pending.id
    assert retried.id == published.id
    assert published.owner_id == editor.id
    assert published.publication_identity is not None
    assert published.publication_identity.username == "retry-editor"
    assert published.status.value == "PUBLISHED"
    assert len(list(playbooks.rglob("*.md"))) == 2


async def test_reserva_expirada_divergente_usa_novo_id_sem_sobrescrever(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    playbooks = tmp_path / "playbooks"
    provider = LocalProvider(playbooks)
    service = _service(repository, provider)
    author = await _user(repository, "stale-author", RoleLevel.SENIOR, "servidores")
    editor = await _user(repository, "stale-editor", RoleLevel.SENIOR, "servidores")
    source = await _publish_base(repository, service, author, "stale-source")
    identity = PublicationIdentity.from_context(_context(editor))
    old_hash = "1" * 64
    old_key = "revision-stale-reservation"
    old = await repository.reserve_revision(
        editor.id,
        source.id,
        source.content_hash,
        old_hash,
        old_key,
        identity,
        ("echo old",),
        datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    await provider.publish(old.id, old.created_at, "conteúdo antigo")

    with pytest.raises(ConflictError):
        await repository.reserve_revision(
            editor.id,
            source.id,
            source.content_hash,
            "2" * 64,
            old_key,
            identity,
            ("echo changed",),
            datetime.now(timezone.utc) + timedelta(seconds=1),
        )

    replacement = await repository.reserve_revision(
        editor.id,
        source.id,
        source.content_hash,
        "2" * 64,
        "revision-stale-replacement",
        identity,
        ("echo changed",),
        datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    replacement_artifact = await provider.publish(
        replacement.id, replacement.created_at, "conteúdo novo"
    )
    assert replacement.content_hash is not None
    assert replacement.idempotency_key is not None
    await repository.mark_revision_published(
        replacement.owner_id,
        replacement.id,
        replacement_artifact.url,
        replacement.content_hash,
        replacement.idempotency_key,
    )

    assert replacement.id != old.id
    assert next(playbooks.rglob(f"{old.id}.md")).read_text() == "conteúdo antigo"
    assert next(playbooks.rglob(f"{replacement.id}.md")).read_text() == "conteúdo novo"
    published_ids = await repository.list_published_runbook_ids(10_000)
    assert old.id not in published_ids
    assert replacement.id in published_ids
    with pytest.raises(ConflictError):
        await repository.list_published_runbook_ids(1)


async def test_precondicao_concorrencia_e_feature_flag(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    author = await _user(repository, "race-author", RoleLevel.SENIOR, "servidores")
    first_editor = await _user(
        repository, "race-editor-one", RoleLevel.SENIOR, "servidores"
    )
    second_editor = await _user(
        repository, "race-editor-two", RoleLevel.SENIOR, "servidores"
    )
    source = await _publish_base(repository, service, author, "race-source")

    with pytest.raises(PreconditionFailedError):
        await service.revise(
            _context(first_editor),
            source.id,
            "0" * 64,
            _markdown("Stale edit", "echo stale"),
            "revision-stale-0001",
        )

    results = await asyncio.gather(
        service.revise(
            _context(first_editor),
            source.id,
            source.content_hash,
            _markdown("Concurrent one", "echo one"),
            "revision-race-0001",
        ),
        service.revise(
            _context(second_editor),
            source.id,
            source.content_hash,
            _markdown("Concurrent two", "echo two"),
            "revision-race-0002",
        ),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, ConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1

    with pytest.raises(ConflictError):
        await service.revise(
            _context(first_editor),
            source.id,
            source.content_hash,
            _markdown("Branch not allowed", "echo branch"),
            "revision-race-0003",
        )

    disabled = _service(
        repository, LocalProvider(playbooks), revisions_enabled=False
    )
    with pytest.raises(ConflictError):
        await disabled.revise(
            _context(first_editor),
            source.id,
            source.content_hash,
            _markdown("Disabled", "echo disabled"),
            "revision-disabled-0001",
        )


def test_if_match_e_payload_sao_estritos() -> None:
    digest = "a" * 64
    assert _parse_if_match(f'"{digest}"') == digest
    for invalid in (digest, f'W/"{digest}"', f' "{digest}"', f'"{digest.upper()}"'):
        with pytest.raises(HTTPException) as captured:
            _parse_if_match(invalid)
        assert captured.value.status_code == 400

    with pytest.raises(PydanticValidationError):
        RevisionRequest.model_validate(
            {"markdown": _markdown("Valid", "echo ok"), "autor": "admin"}
        )

async def test_rbac_entry_roles_enabled_libera_junior_e_pleno_no_proprio_dominio(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = JobService(
        repository,
        _Scanner(),
        LocalProvider(playbooks),
        revisions_enabled=True,
        entry_roles_enabled=True,
    )
    author = await _user(repository, "flag-author", RoleLevel.SENIOR, "servidores")
    junior = await _user(repository, "flag-junior", RoleLevel.JUNIOR, "servidores")
    pleno = await _user(repository, "flag-pleno", RoleLevel.PLENO, "servidores")
    outro = await _user(repository, "flag-junior-redes", RoleLevel.JUNIOR, "redes")
    source = await _publish_base(repository, service, author, "flag-source")
    markdown = _markdown("Run revised command", "echo revised", "Validate output.")

    # Com a flag ligada, ambos revisam dentro do próprio domain_function.
    revision = await service.revise(
        _context(junior), source.id, source.content_hash, markdown, "flag-junior-key"
    )
    assert revision[0].revision_number == 2

    segunda = await service.revise(
        _context(pleno),
        revision[0].id,
        revision[0].content_hash,
        _markdown("Run again", "echo again", "Validate again."),
        "flag-pleno-key",
    )
    assert segunda[0].revision_number == 3

    # A restrição de domínio continua valendo: fora dele, nem existência se revela.
    with pytest.raises(NotFoundError):
        await service.revise(
            _context(outro),
            segunda[0].id,
            segunda[0].content_hash,
            markdown,
            "flag-outro-dominio",
        )

async def test_published_content_devolve_corpo_sem_frontmatter_e_hash(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    """O corpo entregue ao CLI precisa voltar aceito por `revise`."""

    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    autor = await _user(repository, "leitura-autor", RoleLevel.SENIOR, "servidores")
    origem = await _publish_base(repository, service, autor, "leitura-fonte")

    corpo, content_hash = await service.published_content(
        _context(autor), origem.id
    )

    # O Hub gera o frontmatter e recusa o que vier do cliente; devolve-lo
    # convidaria o operador a cola-lo de volta e receber 400.
    assert not corpo.startswith("---")
    assert "nivel_autor:" not in corpo
    assert "ultimo_revisor:" not in corpo
    assert corpo.lstrip().startswith("### ")
    assert content_hash == origem.content_hash

    # O corpo devolvido e aceito de volta sem edicao de estrutura.
    revisao, _ = await service.revise(
        _context(autor),
        origem.id,
        content_hash,
        corpo + "\n> Observacao acrescentada na revisao.\n",
        "leitura-revisao-key",
    )
    assert revisao.revision_number == 2
    assert revisao.supersedes_job_id == origem.id


async def test_published_content_fora_do_dominio_responde_404(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    autor = await _user(repository, "dom-autor", RoleLevel.SENIOR, "servidores")
    outro = await _user(repository, "dom-redes", RoleLevel.SENIOR, "redes")
    origem = await _publish_base(repository, service, autor, "dominio-fonte")

    # 404 e nao 403: confirmar a existencia ja seria vazamento.
    with pytest.raises(NotFoundError):
        await service.published_content(_context(outro), origem.id)


async def test_published_content_exige_papel_de_revisao(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    autor = await _user(repository, "papel-autor", RoleLevel.SENIOR, "servidores")
    junior = await _user(repository, "papel-junior", RoleLevel.JUNIOR, "servidores")
    origem = await _publish_base(repository, service, autor, "papel-fonte")

    with pytest.raises(ForbiddenError):
        await service.published_content(_context(junior), origem.id)


async def test_nome_do_arquivo_de_revisao_segue_o_runbook_de_origem(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    """O caminho completo, ponta a ponta, incluindo a revisão da revisão.

    A terceira revisão nasce da segunda. Se a base do nome fosse o antecessor
    imediato em vez da raiz, sairia `...-version-2-version-3`.
    """
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    author = await _user(repository, "autor-nomes", RoleLevel.SENIOR, "servidores")

    origem = await _publish_base(
        repository, service, author, "rotina-seguranca-jump-lucien"
    )
    assert (
        next(playbooks.rglob(f"*--{origem.id}.md")).name
        == f"rotina-seguranca-jump-lucien--{origem.id}.md"
    )

    segunda, _ = await service.revise(
        _context(author),
        origem.id,
        origem.content_hash,
        _markdown("Run revised command", "echo revised"),
        "nome-revisao-0002",
    )
    assert (
        next(playbooks.rglob(f"*--{segunda.id}.md")).name
        == f"rotina-seguranca-jump-lucien-version-2--{segunda.id}.md"
    )

    terceira, _ = await service.revise(
        _context(author),
        segunda.id,
        segunda.content_hash,
        _markdown("Run revised twice", "echo revised-again"),
        "nome-revisao-0003",
    )
    assert (
        next(playbooks.rglob(f"*--{terceira.id}.md")).name
        == f"rotina-seguranca-jump-lucien-version-3--{terceira.id}.md"
    )
    assert "version-2-version-3" not in str(
        next(playbooks.rglob(f"*--{terceira.id}.md"))
    )


async def test_trilha_distingue_as_duas_recusas_que_o_cliente_nao_distingue(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resposta é a mesma de propósito; a trilha precisa separar.

    "Não existe" e "existe em área que você não alcança" respondem igual porque
    distinguir já confirmaria a existência. Quem investiga chega à diferença
    pelo `request_id`, e a diferença só existe se for registrada.
    """
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    autor = await _user(repository, "dono-acessos", RoleLevel.SENIOR, "acessos")
    forasteiro = await _user(
        repository, "senior-de-redes", RoleLevel.SENIOR, "redes"
    )
    origem = await _publish_base(repository, service, autor, "rotina-de-acesso")

    eventos: list[tuple[str, str, dict[str, str]]] = []

    def capturar(evento: str, actor_id: str, **campos: str) -> None:
        eventos.append((evento, actor_id, campos))

    monkeypatch.setattr("app.application.audit_event", capturar)

    # Existe, mas em área que o ator não alcança.
    with pytest.raises(NotFoundError) as fora_da_area:
        await service.revise(
            _context(forasteiro),
            origem.id,
            origem.content_hash,
            _markdown("Run revised command", "echo revised"),
            "trilha-fora-da-area",
        )

    # Não existe.
    inexistente = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(NotFoundError) as ausente:
        await service.revise(
            _context(forasteiro),
            inexistente,
            "a" * 64,
            _markdown("Run revised command", "echo revised"),
            "trilha-inexistente",
        )

    # O cliente vê exatamente a mesma coisa nos dois casos.
    assert str(fora_da_area.value) == str(ausente.value)

    negadas = [campos for evento, _, campos in eventos if evento == "runbook.revise_negada"]
    assert len(negadas) == 2, eventos
    assert negadas[0]["motivo"] == "fora_do_dominio"
    assert negadas[0]["dominio_do_runbook"] == "acessos"
    assert negadas[0]["dominio_do_ator"] == "redes"
    assert negadas[1]["motivo"] == "fonte_inexistente_ou_nao_publicada"
    assert negadas[1]["source_job_id"] == inexistente

    # A trilha registra a decisão, nunca o conteúdo.
    for campos in negadas:
        assert "markdown" not in campos
        assert "echo revised" not in repr(campos)


async def test_conflito_de_revisao_aponta_a_versao_mais_recente(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    """Dizer só "já possui revisão" deixa quem errou descobrindo sozinho."""
    playbooks = tmp_path / "playbooks"
    service = _service(repository, LocalProvider(playbooks))
    autor = await _user(repository, "autor-linhagem", RoleLevel.SENIOR, "servidores")
    origem = await _publish_base(repository, service, autor, "rotina-com-linhagem")

    segunda, _ = await service.revise(
        _context(autor),
        origem.id,
        origem.content_hash,
        _markdown("Run revised command", "echo revised"),
        "linhagem-0002",
    )
    terceira, _ = await service.revise(
        _context(autor),
        segunda.id,
        segunda.content_hash,
        _markdown("Run revised twice", "echo revised-again"),
        "linhagem-0003",
    )

    # Revisar a raiz depois de a linhagem ter andado duas vezes.
    with pytest.raises(ConflictError) as conflito:
        await service.revise(
            _context(autor),
            origem.id,
            origem.content_hash,
            _markdown("Run revised again", "echo outra-vez"),
            "linhagem-conflito",
        )

    mensagem = str(conflito.value)
    # A ponta da linhagem, e não o sucessor imediato da raiz.
    assert terceira.id in mensagem, mensagem
    assert segunda.id not in mensagem, mensagem
