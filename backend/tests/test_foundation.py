import asyncio
import hashlib
import json
import logging
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.api.schemas import PublishRequest, UploadRequest
from app.application import IdentityService, JobService, UploadProcessor, UploadService
from app.domain.publication import build_frontmatter, validate_playbook
from app.config import Settings
from app.domain.models import (
    Job,
    JobStatus,
    PublicationIdentity,
    RoleLevel,
    RunbookEnrichment,
    RunbookSuggestions,
    SecurityContext,
)
from app.domain.ports import (
    AuthenticationError,
    CommandExtractor,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    SecretDetectedError,
    SecretScanner,
    SecretScanResult,
    StorageProvider,
    RunbookEnricher,
    UpstreamError,
    ValidationError,
)
from app.infrastructure.database import SQLAlchemyJobRepository
from app.domain.dlp import sanitize_secrets
from app.infrastructure.audit import configure_audit_logging
from app.domain.credentials import digest_api_token
from app.infrastructure.storage import (
    LocalProvider,
    git_playbook_relative_path,
    legacy_playbook_relative_paths,
    playbook_relative_path,
)
from app.infrastructure.upload_cipher import AESGCMUploadCipher


class StaticExtractor(CommandExtractor):
    def __init__(self) -> None:
        self.last_description: str | None = None

    async def extract(
        self, sanitized_log: str, sanitized_description: str | None = None
    ) -> tuple[str, ...]:
        assert "segredo-real" not in sanitized_log
        self.last_description = sanitized_description
        # Simula uma IA que reintroduziu uma credencial na resposta.
        return ("docker ps", "kubectl get pods", "REDIS_PASSWORD=slm-secret")


class StaticTagInferrer(RunbookEnricher):
    def __init__(self) -> None:
        self.last_description: str | None = None

    async def infer(
        self,
        commands: tuple[str, ...],
        sanitized_description: str | None = None,
    ) -> RunbookEnrichment:
        self.last_description = sanitized_description
        return RunbookEnrichment(
            inferred_tags=("docker", "kubernetes"),
            suggestions=RunbookSuggestions(
                objective="Validar os serviços de infraestrutura.",
                architecture_prerequisites=("Acesso ao host alvo.",),
                command_impacts=tuple("Consulta sem alteração." for _ in commands),
                rollback_commands=(),
            ),
        )


class UnavailableExtractor(CommandExtractor):
    async def extract(
        self, sanitized_log: str, sanitized_description: str | None = None
    ) -> tuple[str, ...]:
        raise UpstreamError("SLM indisponível")


class BlockingExtractor(CommandExtractor):
    """Permite cancelar o Job enquanto uma extração já está em andamento."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(
        self, sanitized_log: str, sanitized_description: str | None = None
    ) -> tuple[str, ...]:
        self.started.set()
        await self.release.wait()
        return ("docker ps",)


class StaticSecretScanner(SecretScanner):
    def __init__(self, blocked_contents: set[str] | None = None) -> None:
        self.blocked_contents = blocked_contents or set()
        self.scanned_contents: list[str] = []

    async def detect(self, content: str) -> SecretScanResult:
        self.scanned_contents.append(content)
        bloqueado = content in self.blocked_contents
        return SecretScanResult(
            detected=bloqueado, rules=("regra-de-teste",) if bloqueado else ()
        )


class FlakyStorage(StorageProvider):
    """Simula indisponibilidade transitória do destino de publicação."""

    def __init__(self, delegate: StorageProvider, failures: int) -> None:
        self._delegate = delegate
        self.failures = failures

    async def publish(
        self,
        job_id,
        created_at,
        markdown,
        artifact_name=None,
        domain_function=None,
    ):
        if self.failures > 0:
            self.failures -= 1
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

    async def read_bytes(self, relative_path: str) -> bytes:
        return await self._delegate.read_bytes(relative_path)


def context_for(user) -> SecurityContext:
    return SecurityContext.from_user(user)


async def ready_job(
    repository: SQLAlchemyJobRepository,
    owner_id: str,
    name: str,
    raw_log: str,
    *,
    description: str | None = None,
    extractor: CommandExtractor | None = None,
    tag_inferrer: RunbookEnricher | None = None,
    scanner: SecretScanner | None = None,
    enrichment_enabled: bool = True,
):
    """Executa a fronteira assíncrona sem esconder a transição PROCESSING."""

    extractor = extractor or StaticExtractor()
    tag_inferrer = tag_inferrer or StaticTagInferrer()
    scanner = scanner or StaticSecretScanner()
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    intake = UploadService(repository, scanner, cipher, max_log_bytes=1024 * 1024)
    owner = await repository.get_user_by_identifier(owner_id)
    queued = await intake.enqueue(
        context_for(owner), name, raw_log, description
    )
    assert queued.status.value == "PROCESSING"
    processor = UploadProcessor(
        repository,
        cipher,
        extractor,
        tag_inferrer,
        scanner,
        lease_seconds=60,
        retry_base_seconds=1,
        max_attempts=1,
        enrichment_enabled=enrichment_enabled,
    )
    if not await processor.process_once():
        # `process_once` so devolve False quando o claim nao encontrou fila.
        # Um assert nu aqui vira "assert False" e nao diz nada: esta falha ja
        # apareceu uma vez, sob carga, e custou uma investigacao inteira so
        # para descobrir de onde vinha. O estado abaixo responde na proxima.
        atual = await repository.get_job(owner_id, queued.id)
        raise AssertionError(
            "process_once nao reservou nenhum upload. "
            f"job={atual.id} status={atual.status.value} "
            f"erro={atual.processing_error!r} "
            f"enfileirado_em={queued.created_at.isoformat()}"
        )
    return await repository.get_job(owner_id, queued.id)


@pytest.fixture
async def repository(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'hub.db').as_posix()}"
    instance = SQLAlchemyJobRepository(url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


async def test_owner_id_isola_jobs(repository: SQLAlchemyJobRepository) -> None:
    alice = await repository.create_user(
        "alice", "a" * 64, RoleLevel.JUNIOR, "servidores"
    )
    bob = await repository.create_user(
        "bob", "b" * 64, RoleLevel.PLENO, "redes"
    )
    job = await repository.create_job(
        alice.id, "provision-01", ("docker ps",), ("docker",)
    )

    with pytest.raises(NotFoundError):
        await repository.get_job(bob.id, job.id)


async def test_listagem_ativa_isola_usuario_e_inclui_estados_operacionais(
    repository: SQLAlchemyJobRepository,
) -> None:
    alice = await repository.create_user(
        "alice-active", "1" * 64, RoleLevel.JUNIOR, "servidores"
    )
    bob = await repository.create_user(
        "bob-active", "2" * 64, RoleLevel.PLENO, "redes"
    )
    processing = await repository.enqueue_job(
        alice.id, "processando", "fingerprint-processing", "ciphertext"
    )
    pending = await repository.create_job(
        alice.id, "aguardando-revisao", ("docker ps",), ("docker",)
    )
    failed = await repository.enqueue_job(
        alice.id, "falhou", "fingerprint-failed", "ciphertext"
    )
    await repository.fail_upload(failed.id, "UPSTREAM_ERROR")
    await repository.enqueue_job(
        bob.id, "job-do-bob", "fingerprint-bob", "ciphertext"
    )

    active = await repository.list_active(alice.id)

    assert {job.id for job in active} == {processing.id, pending.id, failed.id}
    assert {job.status.value for job in active} == {
        "PROCESSING",
        "PENDING",
        "FAILED",
    }
    assert [job.id for job in await repository.list_pending(alice.id)] == [pending.id]


async def test_upload_assincrono_e_idempotente(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_user(
        "assincrono", "0" * 64, RoleLevel.SENIOR, "servidores"
    )
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    scanner = StaticSecretScanner()
    service = UploadService(repository, scanner, cipher, max_log_bytes=1024 * 1024)

    first = await service.enqueue(context_for(user), "job-assincrono", "docker ps")
    repeated = await service.enqueue(
        context_for(user), "job-assincrono", "docker ps"
    )
    assert first.id == repeated.id
    assert first.status.value == "PROCESSING"

    with pytest.raises(ConflictError):
        await service.enqueue(
            context_for(user), "job-assincrono", "docker images"
        )

    processor = UploadProcessor(
        repository,
        cipher,
        StaticExtractor(),
        StaticTagInferrer(),
        scanner,
        lease_seconds=60,
        retry_base_seconds=1,
        max_attempts=1,
    )
    assert await processor.process_once()
    ready = await repository.get_job(user.id, first.id)
    assert ready.status.value == "PENDING"
    assert ready.commands
    assert not await processor.process_once()


async def test_log_vazio_produz_runbook_visual_sem_chamar_extrator(
    repository: SQLAlchemyJobRepository,
) -> None:
    """Runbook puramente visual: sem sessão de terminal, sem comandos."""

    class ExtractorNuncaChamado(CommandExtractor):
        async def extract(
            self, sanitized_log: str, sanitized_description: str | None = None
        ) -> tuple[str, ...]:
            raise AssertionError(
                "log vazio nao deveria acionar a extracao de comandos"
            )

    user = await repository.create_user(
        "visual", "9" * 64, RoleLevel.SENIOR, "servidores"
    )
    job = await ready_job(
        repository,
        user.id,
        "job-visual",
        "",
        description="Passos de configuração pelo painel web.",
        extractor=ExtractorNuncaChamado(),
    )
    assert job.status.value == "PENDING"
    assert job.commands == ()
    assert job.command_outputs == ()


async def test_enqueue_com_log_vazio_nao_manda_string_vazia_ao_scanner(
    repository: SQLAlchemyJobRepository,
) -> None:
    """Regressão: o serviço de secret scanning exige conteúdo não vazio
    (`min_length=1`) e recusaria a chamada com 422 -- que o Hub converte em
    "scanner indisponível", uma mensagem enganosa para um bug de chamada."""

    user = await repository.create_user(
        "visual-scanner", "6" * 64, RoleLevel.SENIOR, "servidores"
    )
    scanner = StaticSecretScanner()
    job = await ready_job(
        repository,
        user.id,
        "job-visual-scanner",
        "",
        description="Passos de configuração pelo painel web.",
        scanner=scanner,
    )
    assert "" not in scanner.scanned_contents
    assert "Passos de configuração pelo painel web." in scanner.scanned_contents
    assert job.status.value == "PENDING"


async def test_log_nao_vazio_sem_comandos_extraidos_ainda_falha(
    repository: SQLAlchemyJobRepository,
) -> None:
    """Regressão: log de verdade sem nenhum comando reconhecido continua
    sendo uma falha real, não um runbook visual disfarçado."""

    class ExtractorSemComandos(CommandExtractor):
        async def extract(
            self, sanitized_log: str, sanitized_description: str | None = None
        ) -> tuple[str, ...]:
            return ()

    user = await repository.create_user(
        "log-sem-comando", "7" * 64, RoleLevel.SENIOR, "servidores"
    )
    job = await ready_job(
        repository,
        user.id,
        "job-sem-comando",
        "texto qualquer que nao parece comando de terminal",
        extractor=ExtractorSemComandos(),
    )
    assert job.status.value == "FAILED"
    assert job.processing_error == "NO_COMMANDS"


async def test_payload_cifrado_e_vinculado_ao_proprietario() -> None:
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    sealed = cipher.seal("owner-a", "job-a", "PASSWORD=redigida", None)

    assert "PASSWORD" not in sealed.ciphertext
    assert cipher.open("owner-a", "job-a", sealed.ciphertext) == (
        "PASSWORD=redigida",
        None,
    )
    with pytest.raises(ValidationError):
        cipher.open("owner-b", "job-a", sealed.ciphertext)


async def test_worker_persiste_somente_saidas_limitadas_e_sanitizadas(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_user(
        "outputworker", "8" * 64, RoleLevel.SENIOR, "servidores"
    )
    job = await ready_job(
        repository,
        user.id,
        "job-com-saidas",
        """operador@host:~$ docker ps
docker ps
PASSWORD=segredo-real
operador@host:~$ kubectl get pods
kubectl get pods
pod-a Running
operador@host:~$ lucien stop
""",
    )

    assert job.command_outputs[0] == "PASSWORD=SUA_SENHA_AQUI"
    assert job.command_outputs[1] == "pod-a Running"
    assert job.command_outputs[2] == ""


async def test_job_falha_e_pode_ser_reenfileirado(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_user(
        "retryworker", "1" * 64, RoleLevel.SENIOR, "servidores"
    )
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    scanner = StaticSecretScanner()
    service = UploadService(repository, scanner, cipher, max_log_bytes=1024 * 1024)
    job = await service.enqueue(context_for(user), "job-retry", "docker ps")

    failing = UploadProcessor(
        repository,
        cipher,
        UnavailableExtractor(),
        StaticTagInferrer(),
        scanner,
        lease_seconds=60,
        retry_base_seconds=1,
        max_attempts=1,
    )
    assert await failing.process_once()
    failed = await repository.get_job(user.id, job.id)
    assert failed.status.value == "FAILED"
    assert failed.processing_error == "UPSTREAM_ERROR"

    queued_again = await service.retry(user.id, job.id)
    assert queued_again.status.value == "PROCESSING"
    succeeding = UploadProcessor(
        repository,
        cipher,
        StaticExtractor(),
        StaticTagInferrer(),
        scanner,
        lease_seconds=60,
        retry_base_seconds=1,
        max_attempts=1,
    )
    assert await succeeding.process_once()
    assert (await repository.get_job(user.id, job.id)).status.value == "PENDING"


async def test_publicacao_repetida_e_idempotente(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "operador", "c" * 64, RoleLevel.SENIOR, "servidores"
    )
    extractor = StaticExtractor()
    tag_inferrer = StaticTagInferrer()
    scanner = StaticSecretScanner()
    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    job = await ready_job(
        repository,
        user.id,
        "cluster-20260101",
        "PASSWORD=segredo-real\ndocker ps",
        description="  Diagnosticar Redis com REDIS_PASSWORD=segredo-descricao  ",
        extractor=extractor,
        tag_inferrer=tag_inferrer,
        scanner=scanner,
    )
    assert extractor.last_description == (
        "Diagnosticar Redis com REDIS_PASSWORD=SUA_SENHA_REDIS_AQUI"
    )
    assert tag_inferrer.last_description == extractor.last_description
    assert "slm-secret" not in "\n".join(job.commands)
    assert "REDIS_PASSWORD=SUA_SENHA_REDIS_AQUI" in job.commands
    assert job.runbook_suggestions.objective == (
        "Validar os serviços de infraestrutura."
    )
    assert len(job.runbook_suggestions.command_impacts) == len(job.commands)
    markdown = """# Operação

REDIS_PASSWORD=segredo-final

### Passo 1: Listar contêineres
```bash
docker ps
```
> Confirme que os contêineres esperados estão ativos.
"""
    first, first_count = await service.publish(
        context_for(user), job.id, markdown, "retry-key-0001"
    )
    second, second_count = await service.publish(
        context_for(user), job.id, markdown, "retry-key-0001"
    )

    assert first.status.value == "PUBLISHED"
    assert second.storage_url == first.storage_url
    assert first_count == second_count == 1
    published_files = list((tmp_path / "playbooks").rglob("*.md"))
    assert len(published_files) == 1
    assert published_files[0].relative_to(tmp_path / "playbooks").as_posix() == (
        f"{job.created_at.year}/servidores/cluster-20260101--{job.id}.md"
    )
    published_content = published_files[0].read_text(encoding="utf-8")
    assert "segredo-final" not in published_content
    assert "REDIS_PASSWORD=SUA_SENHA_REDIS_AQUI" in published_content
    assert f'id: "{job.id}"' in published_content
    assert 'autor: "operador"' in published_content
    assert 'nivel_autor: "senior"' in published_content
    assert 'funcao: "servidores"' in published_content
    assert 'tags_inferidas: ["docker", "kubernetes"]' in published_content
    frontmatter = published_content.splitlines()[:11]
    assert frontmatter[0] == "---"
    assert [line.split(":", 1)[0] for line in frontmatter[1:10]] == [
        "id",
        "autor",
        "nivel_autor",
        "funcao",
        "data_criacao",
        "tags_inferidas",
        "versao",
        "ultimo_revisor",
        "data_revisao",
    ]
    assert frontmatter[10] == "---"
    # Nascem vazios: sao preenchidos a mao por quem corrigir o arquivo no repositorio.
    assert 'versao: "1"' in published_content
    assert 'ultimo_revisor: ""' in published_content
    assert 'data_revisao: ""' in published_content

    with pytest.raises(ConflictError):
        await service.publish(
            context_for(user), job.id, markdown + "\nmudou", "retry-key-0002"
        )


async def test_bootstrap_concorrente_cria_somente_um_admin(
    repository: SQLAlchemyJobRepository,
) -> None:
    first_service = IdentityService(repository, "pepper-de-teste")
    second_service = IdentityService(repository, "pepper-de-teste")

    results = await asyncio.gather(
        first_service.bootstrap_admin("admin-a", "plataforma"),
        second_service.bootstrap_admin("admin-b", "plataforma"),
        return_exceptions=True,
    )

    created = [item for item in results if not isinstance(item, BaseException)]
    conflicts = [item for item in results if isinstance(item, ConflictError)]
    assert len(created) == 1
    assert len(conflicts) == 1
    assert await repository.count_active_admins() == 1


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="PostgreSQL de integração não configurado",
)
async def test_bootstrap_postgresql_serializa_repositorios_independentes() -> None:
    """Valida o latch transacional entre workers que não compartilham memória."""
    database_url = os.environ["POSTGRES_TEST_DATABASE_URL"]
    first_repository = SQLAlchemyJobRepository(database_url)
    second_repository = SQLAlchemyJobRepository(database_url)
    await first_repository.initialize()
    await second_repository.initialize()
    try:
        results = await asyncio.gather(
            IdentityService(first_repository, "pepper-de-teste").bootstrap_admin(
                "admin-worker-a", "plataforma"
            ),
            IdentityService(second_repository, "pepper-de-teste").bootstrap_admin(
                "admin-worker-b", "plataforma"
            ),
            return_exceptions=True,
        )

        created = [item for item in results if not isinstance(item, BaseException)]
        conflicts = [item for item in results if isinstance(item, ConflictError)]
        assert len(created) == 1
        assert len(conflicts) == 1
        assert await first_repository.count_active_admins() == 1
    finally:
        await first_repository.close()
        await second_repository.close()


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="PostgreSQL de integração não configurado",
)
async def test_postgresql_entrega_job_a_um_unico_worker() -> None:
    database_url = os.environ["POSTGRES_TEST_DATABASE_URL"]
    first_repository = SQLAlchemyJobRepository(database_url)
    second_repository = SQLAlchemyJobRepository(database_url)
    await first_repository.initialize()
    await second_repository.initialize()
    try:
        user = await first_repository.create_user(
            "worker-queue", "2" * 64, RoleLevel.SENIOR, "servidores"
        )
        cipher = AESGCMUploadCipher("test-secret-" * 4)
        sealed = cipher.seal(user.id, "queue-race", "docker ps", None)
        job = await first_repository.enqueue_job(
            user.id, "queue-race", sealed.fingerprint, sealed.ciphertext
        )
        now = datetime.now(UTC)
        claims = await asyncio.gather(
            first_repository.claim_next_upload(now, now + timedelta(minutes=5)),
            second_repository.claim_next_upload(now, now + timedelta(minutes=5)),
        )
        delivered = [claim for claim in claims if claim is not None]
        assert len(delivered) == 1
        assert delivered[0].job_id == job.id
        await first_repository.complete_upload(
            job.id,
            ("docker ps",),
            ("CONTAINER ID",),
            RunbookSuggestions("", (), ("",), ()),
            ("docker",),
        )
    finally:
        await first_repository.close()
        await second_repository.close()


async def test_auditoria_registra_mutacoes_sem_segredos(
    repository: SQLAlchemyJobRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = IdentityService(repository, "pepper-de-teste")

    with caplog.at_level(logging.INFO, logger="lucien.audit"):
        admin, admin_token = await service.bootstrap_admin(
            "root-admin", "plataforma"
        )
        _, provisional_token, _ = await service.create_user(
            SecurityContext.from_user(admin),
            "operador-x",
            RoleLevel.PLENO,
            "servidores",
        )

    events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "lucien.audit"
    ]
    assert [event["event"] for event in events] == ["user.bootstrap", "user.create"]
    assert events[1]["actor_id"] == admin.id
    trail = json.dumps(events)
    assert admin_token not in trail
    assert provisional_token not in trail


async def test_recuperacao_offline_rotaciona_apenas_admin_ativo(
    repository: SQLAlchemyJobRepository,
) -> None:
    pepper = "pepper-de-recuperacao"
    service = IdentityService(repository, pepper)
    admin, old_token = await service.bootstrap_admin("admin-recovery", "plataforma")

    recovered, provisional_token, expires_at = await service.recover_admin_token(
        admin.username
    )

    assert recovered.id == admin.id
    assert provisional_token.startswith("luc_tmp_")
    assert expires_at > datetime.now(UTC)
    assert (
        await repository.find_user_by_token_hash(digest_api_token(old_token, pepper))
        is None
    )
    exchanged, new_token = await service.exchange_provisional_token(
        provisional_token, "recovery-exchange-001"
    )
    assert exchanged.id == admin.id
    assert new_token != old_token
    assert (
        await repository.find_user_by_token_hash(digest_api_token(new_token, pepper))
    ).id == admin.id

    operator, _, _ = await service.create_user(
        SecurityContext.from_user(admin),
        "operador-recovery",
        RoleLevel.SENIOR,
        "servidores",
    )
    with pytest.raises(ForbiddenError):
        await service.recover_admin_token(operator.username)


async def test_token_provisorio_expira_e_nao_pode_ser_reutilizado(
    repository: SQLAlchemyJobRepository,
) -> None:
    pepper = "pepper-expiracao"
    provisional = "luc_tmp_token-expirado"
    provisional_hash = digest_api_token(provisional, pepper)
    await repository.create_provisioned_user(
        "usuario-expirado",
        provisional_hash,
        datetime.now(UTC) - timedelta(seconds=1),
        RoleLevel.JUNIOR,
        "servidores",
    )

    with pytest.raises(AuthenticationError, match="expirado"):
        await repository.exchange_provisional_token(
            provisional_hash,
            digest_api_token("luc_permanente", pepper),
            digest_api_token("exchange:expired-key-001", pepper),
            datetime.now(UTC),
        )

    with pytest.raises(AuthenticationError, match="invalid provisional token"):
        await repository.exchange_provisional_token(
            provisional_hash,
            digest_api_token("luc_outro", pepper),
            digest_api_token("exchange:expired-key-002", pepper),
            datetime.now(UTC),
        )


def test_auditoria_configura_stdout() -> None:
    logger = logging.getLogger("lucien.audit")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    try:
        logger.handlers.clear()
        configure_audit_logging()
        assert logger.handlers[0].stream is sys.stdout
    finally:
        logger.handlers.clear()
        logger.handlers.extend(original_handlers)
        logger.setLevel(original_level)
        logger.propagate = original_propagate


async def test_falha_de_storage_nao_prende_conteudo_da_reserva(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "resiliente", "h" * 64, RoleLevel.SENIOR, "servidores"
    )
    storage = FlakyStorage(LocalProvider(tmp_path / "playbooks"), failures=1)
    scanner = StaticSecretScanner()
    service = JobService(repository, scanner, storage)
    job = await ready_job(repository, user.id, "job-flaky", "docker ps", scanner=scanner)
    first_draft = "### Passo 1: Listar\n```bash\ndocker ps\n```\n"
    revised_draft = (
        "### Passo 1: Listar\n```bash\ndocker ps\n```\n"
        "> Confirme os contêineres esperados.\n"
    )

    # Primeira tentativa falha no storage e deixa a reserva registrada.
    with pytest.raises(UpstreamError):
        await service.publish(
            context_for(user), job.id, first_draft, "retry-key-flaky-01"
        )
    assert (await repository.get_job(user.id, job.id)).status.value == "PENDING"

    # O rascunho editado (nova chave/conteúdo) deve substituir a reserva anterior.
    published, _ = await service.publish(
        context_for(user), job.id, revised_draft, "retry-key-flaky-02"
    )
    assert published.status.value == "PUBLISHED"
    published_files = list((tmp_path / "playbooks").rglob("*.md"))
    assert len(published_files) == 1
    assert "Confirme os contêineres esperados." in published_files[0].read_text(
        encoding="utf-8"
    )

    # Após PUBLISHED, a imutabilidade continua valendo para conteúdo divergente.
    with pytest.raises(ConflictError):
        await service.publish(
            context_for(user), job.id, first_draft, "retry-key-flaky-03"
        )


async def test_mesma_chave_nao_aceita_outro_conteudo_enquanto_pending(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_user(
        "idempotente", "i" * 64, RoleLevel.SENIOR, "servidores"
    )
    job = await repository.create_job(
        user.id, "job-idempotente", ("echo ok",), ("shell",)
    )
    identity = PublicationIdentity.from_context(context_for(user))
    await repository.reserve_publication(
        user.id, job.id, "1" * 64, "retry-key-fixed-01", identity
    )

    with pytest.raises(ConflictError):
        await repository.reserve_publication(
            user.id, job.id, "2" * 64, "retry-key-fixed-01", identity
        )


async def test_provider_local_rejeita_publicacoes_concorrentes_divergentes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = LocalProvider(tmp_path / "playbooks")
    barrier = threading.Barrier(2)
    original_exists = Path.exists

    def exists_after_barrier(path: Path) -> bool:
        # Força as duas threads a passarem pela consulta inicial antes de publicar.
        if path.suffix == ".md":
            barrier.wait(timeout=5)
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", exists_after_barrier)
    results = await asyncio.gather(
        provider.publish(
            "12345678-1234-1234-1234-123456789abc",
            datetime(2026, 7, 22, tzinfo=UTC),
            "conteudo-a",
        ),
        provider.publish(
            "12345678-1234-1234-1234-123456789abc",
            datetime(2026, 7, 22, tzinfo=UTC),
            "conteudo-b",
        ),
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, BaseException)]
    conflicts = [item for item in results if isinstance(item, ConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1


async def test_job_publicado_nao_pode_ser_expurgado(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "auditor", "d" * 64, RoleLevel.SENIOR, "banco_de_dados"
    )
    scanner = StaticSecretScanner()
    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    job = await ready_job(repository, user.id, "job-audit", "docker ps", scanner=scanner)
    markdown = "### Passo 1: Auditar\n```bash\ndocker ps\n```\n"
    await service.publish(
        context_for(user), job.id, markdown, "retry-key-0003"
    )

    with pytest.raises(ConflictError):
        await service.delete(user.id, job.id)

    with pytest.raises(ConflictError):
        await service.delete(user.id, job.id, force=True)


async def test_force_cancela_job_em_processamento_e_remove_fila(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "cancelador", "7" * 64, RoleLevel.SENIOR, "servidores"
    )
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    scanner = StaticSecretScanner()
    upload_service = UploadService(
        repository, scanner, cipher, max_log_bytes=1024 * 1024
    )
    job_service = JobService(
        repository, scanner, LocalProvider(tmp_path / "playbooks")
    )
    job = await upload_service.enqueue(
        context_for(user), "cancelar-processamento", "docker ps"
    )

    with pytest.raises(ConflictError):
        await job_service.delete(user.id, job.id)

    await job_service.delete(user.id, job.id, force=True)

    with pytest.raises(NotFoundError):
        await repository.get_job(user.id, job.id)
    now = datetime.now(UTC)
    assert await repository.claim_next_upload(
        now, now + timedelta(minutes=1)
    ) is None


async def test_worker_encerra_sem_ressuscitar_job_cancelado(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "cancelamento-race", "8" * 64, RoleLevel.SENIOR, "servidores"
    )
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    scanner = StaticSecretScanner()
    extractor = BlockingExtractor()
    upload_service = UploadService(
        repository, scanner, cipher, max_log_bytes=1024 * 1024
    )
    job_service = JobService(
        repository, scanner, LocalProvider(tmp_path / "playbooks")
    )
    processor = UploadProcessor(
        repository,
        cipher,
        extractor,
        StaticTagInferrer(),
        scanner,
        lease_seconds=60,
        retry_base_seconds=1,
        max_attempts=1,
    )
    job = await upload_service.enqueue(
        context_for(user), "cancelamento-concorrente", "docker ps"
    )

    processing = asyncio.create_task(processor.process_once())
    await asyncio.wait_for(extractor.started.wait(), timeout=1)
    await job_service.delete(user.id, job.id, force=True)
    extractor.release.set()

    assert await asyncio.wait_for(processing, timeout=1)
    with pytest.raises(NotFoundError):
        await repository.get_job(user.id, job.id)


async def test_junior_nao_publica_operacao_de_criticidade_alta(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "junior", "e" * 64, RoleLevel.JUNIOR, "servidores"
    )
    scanner = StaticSecretScanner()
    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    job = await ready_job(repository, user.id, "limpeza", "rm -rf /tmp/cache", scanner=scanner)
    markdown = "### Passo 1: Limpar cache\n```bash\nrm -rf /tmp/cache\n```\n"

    with pytest.raises(ForbiddenError):
        await service.publish(
            context_for(user), job.id, markdown, "retry-key-high-0001"
        )
    assert not list((tmp_path / "playbooks").rglob("*.md"))


async def test_frontmatter_enviado_pelo_cliente_e_rejeitado(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "spoof", "f" * 64, RoleLevel.SENIOR, "redes"
    )
    scanner = StaticSecretScanner()
    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    job = await ready_job(repository, user.id, "spoof-job", "ip addr", scanner=scanner)
    forged = """---
autor: "admin"
nivel_autor: "admin"
---
### Passo 1: Inspecionar rede
```bash
ip addr
```
"""

    with pytest.raises(ValidationError):
        await service.publish(
            context_for(user), job.id, forged, "retry-key-spoof-01"
        )


async def test_secret_scanner_em_enforce_bloqueia_upload_e_publicacao(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    user = await repository.create_user(
        "seguranca", "g" * 64, RoleLevel.SENIOR, "servidores"
    )
    scanner = StaticSecretScanner({"MARCADOR_BLOQUEADO"})
    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    intake = UploadService(
        repository,
        scanner,
        AESGCMUploadCipher("test-secret-" * 4),
        max_log_bytes=1024 * 1024,
    )

    with pytest.raises(SecretDetectedError):
        await intake.enqueue(
            context_for(user), "bloqueado", "MARCADOR_BLOQUEADO"
        )
    assert scanner.scanned_contents == ["MARCADOR_BLOQUEADO"]

    job = await ready_job(
        repository, user.id, "permitido", "docker ps", scanner=scanner
    )
    scanner.blocked_contents.add("### Passo 1: Validar\n```bash\ndocker ps\n```\nMARCADOR_BLOQUEADO")
    with pytest.raises(SecretDetectedError):
        await service.publish(
            context_for(user),
            job.id,
            "### Passo 1: Validar\n```bash\ndocker ps\n```\nMARCADOR_BLOQUEADO",
            "retry-key-enforce-01",
        )
    assert (await repository.get_job(user.id, job.id)).status.value == "PENDING"
    assert not list((tmp_path / "playbooks").rglob("*.md"))


def test_sanitizacao_substitui_formatos_criticos_sem_expor_valores() -> None:
    content = """
Authorization: Bearer token-super-secreto
REDIS_USER=admin-real
REDIS_PASSWORD=senha-real
EVOLUTION_API_KEY=evolution-real
{"password": "senha-json", "token": "token-json"}
DATABASE_URL=redis://usuario-real:senha-url@redis:6379/0
redis-cli AUTH senha-auth
curl --token token-cli
curl --password "senha-cli-aspas" # gitleaks:allow
ghp_AAAAAAAAAAAAAAAAAAAAAAAA
luc_tmp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
luc_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
-----BEGIN PRIVATE KEY-----
material-privado
-----END PRIVATE KEY-----
"""

    result = sanitize_secrets(content)

    for secret in (
        "token-super-secreto",
        "admin-real",
        "senha-real",
        "evolution-real",
        "senha-json",
        "token-json",
        "usuario-real",
        "senha-url",
        "senha-auth",
        "token-cli",
        "senha-cli-aspas",
        "material-privado",
        "luc_tmp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "luc_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    ):
        assert secret not in result.text
    assert "SEU_USER_REDIS_AQUI" in result.text
    assert "SUA_SENHA_REDIS_AQUI" in result.text
    assert "SUA_KEY_EVOLUTION_AQUI" in result.text
    assert result.text.count("SEU_TOKEN_LUCIEN_AQUI") == 2
    assert "SUA_CHAVE_PRIVADA_AQUI" in result.text
    assert result.replacements >= 8


def test_gramatica_ignora_fences_de_exemplo() -> None:
    markdown = (
        "### Passo 1: Documentar o padrão\n"
        "```bash\necho ok\n```\n"
        "> A gramática esperada é exemplificada abaixo.\n"
        "\n"
        "````markdown\n"
        "### Passo 99: Exemplo ilustrativo\n"
        "```bash\ncomando-de-exemplo\n```\n"
        "````\n"
    )

    validated = validate_playbook(markdown)

    assert validated.command_blocks == ("echo ok",)


def test_gramatica_aceita_template_em_ingles_do_cli() -> None:
    markdown = "### Step 1: Check service\n```bash\nsystemctl status sshd\n```\n"

    validated = validate_playbook(markdown)

    assert validated.command_blocks == ("systemctl status sshd",)


def test_gramatica_rejeita_fence_sem_fechamento() -> None:
    markdown = (
        "### Passo 1: Guia\n```bash\necho ok\n```\n"
        "```yaml\nchave: valor\n"
    )

    with pytest.raises(ValidationError):
        validate_playbook(markdown)


def test_gramatica_aceita_passo_visual_com_imagem() -> None:
    """Passo sem comando é válido quando documenta a ação com uma imagem --
    é a evidência mínima, já que não há comando nenhum para revisar."""

    markdown = (
        "### Passo 1: Rodar o diagnóstico\n"
        "```bash\necho ok\n```\n"
        "### Passo 2: Clique em Confirmar\n"
        "\n"
        "![Botão Confirmar destacado](assets/11111111-1111-1111-1111-111111111111/img.png)\n"
    )

    validated = validate_playbook(markdown)

    assert validated.command_blocks == ("echo ok",)


def test_gramatica_aceita_runbook_totalmente_visual() -> None:
    """Zero comandos, só passos visuais -- ainda assim publicável."""

    from app.domain.publication import Criticality

    markdown = (
        "### Passo 1: Abra o painel\n"
        "\n"
        "![Painel inicial](assets/11111111-1111-1111-1111-111111111111/a.png)\n"
        "### Passo 2: Clique em Salvar\n"
        "\n"
        "![Botão Salvar](assets/11111111-1111-1111-1111-111111111111/b.png)\n"
    )

    validated = validate_playbook(markdown)

    assert validated.command_blocks == ()
    assert validated.criticality == Criticality.LOW


def test_gramatica_rejeita_passo_sem_bash_e_sem_imagem() -> None:
    """Texto solto sob um "### Passo" não é passo revisável -- nem comando,
    nem evidência visual do que foi feito."""

    markdown = "### Passo 1: Faça o procedimento manualmente\n\nSem imagem nenhuma aqui.\n"

    with pytest.raises(ValidationError):
        validate_playbook(markdown)


def test_dlp_redis_auth_preserva_prosa_e_redige_comandos() -> None:
    content = (
        "O fluxo de auth segue o padrão da empresa.\n"
        "Configure o AUTH header antes de continuar.\n"
        "### AUTH no Redis\n"
        "> AUTH é descrito no procedimento.\n"
        "127.0.0.1:6379> AUTH senha-interativa\n"
        "redis-cli -h cache AUTH senha-cli\n"
        "AUTH senha-inicio-de-linha\n"
    )

    result = sanitize_secrets(content)

    assert "O fluxo de auth segue o padrão da empresa." in result.text
    assert "Configure o AUTH header antes de continuar." in result.text
    assert "### AUTH no Redis" in result.text
    assert "> AUTH é descrito no procedimento." in result.text
    for secret in ("senha-interativa", "senha-cli", "senha-inicio-de-linha"):
        assert secret not in result.text
    assert result.text.count("SUA_SENHA_REDIS_AQUI") == 3


def test_caminho_git_fica_na_arvore_do_mkdocs() -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    job_id = "12345678-1234-1234-1234-123456789abc"

    relative = git_playbook_relative_path(
        "docs/runbooks", job_id, created_at, domain_function="servidores"
    )

    assert relative.as_posix() == f"docs/runbooks/2026/servidores/{job_id}.md"


def test_caminho_git_expoe_nome_limpo_sem_perder_identidade() -> None:
    created_at = datetime(2026, 8, 13, tzinfo=UTC)
    job_id = "b8b6e6a1-5bd9-47cc-8a50-df1bea1a4055"

    relative = git_playbook_relative_path(
        "docs/runbooks",
        job_id,
        created_at,
        "teste-uso_1-20260813-001602-7093b5c3e42d",
        "servidores",
    )

    assert relative.as_posix() == (
        f"docs/runbooks/2026/servidores/teste-uso_1--{job_id}.md"
    )


@pytest.mark.parametrize(
    "url",
    ("scanner:8090", "ftp://scanner", "http://scanner/caminho", "http://scanner:65536"),
)
def test_url_do_secret_scanner_rejeita_destinos_inseguros(url: str) -> None:
    with pytest.raises(ValueError):
        Settings.validate_secret_scanner_url(url)


@pytest.mark.parametrize(
    "prefix",
    ("/etc", "../docs", r"docs\runbooks", "docs/runbooks?ref=main"),
)
def test_prefixo_git_rejeita_caminhos_inseguros(prefix: str) -> None:
    with pytest.raises(ValueError):
        Settings.validate_git_docs_prefix(prefix)


def test_payload_publicacao_rejeita_metadados_de_identidade() -> None:
    with pytest.raises(PydanticValidationError):
        PublishRequest.model_validate(
            {
                "markdown": "### Passo 1: Teste\n```bash\necho ok\n```",
                "autor": "admin-forjado",
                "role_level": "admin",
            }
        )


def test_payload_upload_normaliza_descricao_opcional() -> None:
    without_description = UploadRequest.model_validate(
        {"name": "redis", "raw_log": "redis-cli ping"}
    )
    with_description = UploadRequest.model_validate(
        {
            "name": "redis",
            "raw_log": "redis-cli ping",
            "description": "  Diagnosticar   latência\nno Redis  ",
        }
    )

    assert without_description.description is None
    assert with_description.description == "Diagnosticar latência no Redis"

    with pytest.raises(PydanticValidationError):
        UploadRequest.model_validate(
            {
                "name": "redis",
                "raw_log": "redis-cli ping",
                "description": "x" * 281,
            }
        )


class UnavailableTagInferrer(RunbookEnricher):
    async def infer(
        self,
        commands: tuple[str, ...],
        sanitized_description: str | None = None,
    ) -> RunbookEnrichment:
        raise UpstreamError("SLM indisponível para enriquecimento")


async def test_enriquecimento_indisponivel_nao_derruba_o_job(repository) -> None:
    owner = await repository.create_user(
        f"dono-{id(repository)}", "e" * 64, RoleLevel.PLENO, "plataforma"
    )
    job = await ready_job(
        repository,
        owner.id,
        "fallback-enriquecimento",
        "$ docker ps\n$ kubectl get pods\n",
        description="Diagnosticar latência no cache Redis",
        tag_inferrer=UnavailableTagInferrer(),
    )

    # A extração é insubstituível; o enriquecimento é auxiliar e pode faltar.
    assert job.status.value == "PENDING"
    assert job.commands[:2] == ("docker ps", "kubectl get pods")
    assert job.inferred_tags == ()
    assert job.runbook_suggestions.objective == ""
    assert job.runbook_suggestions.rollback_commands == ()
    assert job.runbook_suggestions.command_impacts == ("",) * len(job.commands)
    assert job.description == "Diagnosticar latência no cache Redis"


async def test_enriquecimento_desligado_nao_chama_a_slm(repository) -> None:
    owner = await repository.create_user(
        f"dono-{id(repository)}", "e" * 64, RoleLevel.PLENO, "plataforma"
    )
    inferrer = StaticTagInferrer()
    job = await ready_job(
        repository,
        owner.id,
        "enriquecimento-desligado",
        "$ docker ps\n$ kubectl get pods\n",
        description="Sessão sem enriquecimento",
        tag_inferrer=inferrer,
        enrichment_enabled=False,
    )

    assert job.status.value == "PENDING"
    assert inferrer.last_description is None, "a SLM não deveria ter sido consultada"
    assert job.inferred_tags == ()
    assert job.description == "Sessão sem enriquecimento"


async def test_skip_enrichment_por_job_ignora_a_slm_mesmo_habilitada(
    repository,
) -> None:
    owner = await repository.create_user(
        "dono-skip", "f" * 64, RoleLevel.PLENO, "plataforma"
    )
    inferrer = StaticTagInferrer()
    scanner = StaticSecretScanner()
    cipher = AESGCMUploadCipher("test-secret-" * 4)
    intake = UploadService(repository, scanner, cipher, max_log_bytes=1024 * 1024)
    queued = await intake.enqueue(
        context_for(owner),
        "opt-out-do-operador",
        "$ docker ps\n",
        "Sessão com --skip-enrichment",
        True,
    )
    processor = UploadProcessor(
        repository,
        cipher,
        StaticExtractor(),
        inferrer,
        scanner,
        lease_seconds=60,
        retry_base_seconds=1,
        max_attempts=1,
        # Habilitado globalmente: o opt-out do Job precisa prevalecer sozinho.
        enrichment_enabled=True,
    )
    assert await processor.process_once()
    job = await repository.get_job(owner.id, queued.id)

    assert job.status.value == "PENDING"
    assert inferrer.last_description is None, "a SLM não deveria ter sido consultada"
    assert job.inferred_tags == ()
    assert job.description == "Sessão com --skip-enrichment"


async def test_retry_sem_flag_preserva_a_escolha_do_upload(repository) -> None:
    owner = await repository.create_user(
        "dono-retry", "a" * 64, RoleLevel.PLENO, "plataforma"
    )
    job = await repository.enqueue_job(
        owner.id, "retry-preserva", "fingerprint-retry", "ciphertext", True
    )
    await repository.fail_upload(job.id, "UPSTREAM_ERROR")

    await repository.retry_failed_upload(
        owner.id, job.id, datetime.now(UTC), None
    )
    claimed = await repository.claim_next_upload(
        datetime.now(UTC), datetime.now(UTC) + timedelta(seconds=60)
    )
    assert claimed is not None
    assert claimed.skip_enrichment is True, "retry sem flag não pode reativar a SLM"


def test_tabela_de_risco_cobre_comandos_de_rede() -> None:
    from app.domain.publication import Criticality, classify_criticality

    for comando in (
        "reload",
        "admin reboot",
        "reset saved-configuration",
        "write erase",
        "ont delete 0 1 5",
        "undo onu 1",
        "clear ip bgp *",
        # `no shutdown` herda o padrão amplo de `shutdown`, já existente.
        "no shutdown",
    ):
        assert classify_criticality([comando]) is Criticality.HIGH, comando

    for comando in (
        "configure terminal",
        "system-view",
        "commit",
        "admin save",
        "write memory",
        "rollback",
    ):
        assert classify_criticality([comando]) is Criticality.MEDIUM, comando

    # Leitura permanece baixa: a mudança só restringe o que já era destrutivo.
    for comando in ("show cable modem", "display ont info 0 1", "show router bgp summary"):
        assert classify_criticality([comando]) is Criticality.LOW, comando


def test_rbac_entry_roles_enabled_controla_publicacao_de_criticidade_alta() -> None:
    from app.domain.publication import Criticality, authorize_publication

    # Default: bloqueado, como antes da flag existir.
    with pytest.raises(ForbiddenError):
        authorize_publication(RoleLevel.JUNIOR, Criticality.HIGH)
    with pytest.raises(ForbiddenError):
        authorize_publication(RoleLevel.JUNIOR, Criticality.HIGH, False)

    # Habilitado: junior publica alta.
    authorize_publication(RoleLevel.JUNIOR, Criticality.HIGH, True)

    # A flag não altera os demais papéis nem as criticidades menores.
    authorize_publication(RoleLevel.JUNIOR, Criticality.MEDIUM)
    for papel in (RoleLevel.PLENO, RoleLevel.SENIOR, RoleLevel.ADMIN):
        authorize_publication(papel, Criticality.HIGH)


def test_leitura_encontra_publicacao_no_layout_antigo() -> None:
    """Revisar um runbook publicado antes da inversão precisa continuar possível.

    O artefato é imutável e a URL dele pode já estar anotada em algum lugar,
    então publicações antigas ficam onde estão. Só a leitura procura nos dois.
    """

    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    job_id = "12345678-1234-1234-1234-123456789abc"

    atual = playbook_relative_path(
        job_id, created_at, domain_function="servidores"
    )
    legados = legacy_playbook_relative_paths(
        job_id, created_at, domain_function="servidores"
    )
    legado = legados[0]

    assert atual.as_posix() == f"2026/servidores/{job_id}.md"
    assert legado.as_posix() == f"servidores/2026/{job_id}.md"
    # Mesmo arquivo, diretórios trocados: o nome não pode divergir.
    assert legado.name == atual.name


async def test_local_le_publicacao_gravada_no_layout_antigo(tmp_path: Path) -> None:
    provider = LocalProvider(tmp_path / "playbooks")
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    job_id = "12345678-1234-1234-1234-123456789abc"

    antigo = tmp_path / "playbooks" / "servidores" / "2026"
    antigo.mkdir(parents=True)
    (antigo / f"{job_id}.md").write_text("### Passo 1\n", encoding="utf-8")

    conteudo = await provider.read_published(
        job_id, created_at, domain_function="servidores"
    )

    assert conteudo == "### Passo 1\n"


async def _usuario(repository, username: str, role, domain: str):
    token_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return await repository.create_user(username, token_hash, role, domain)


def _intake(repository, domains: tuple[str, ...]):
    return UploadService(
        repository,
        StaticSecretScanner(),
        AESGCMUploadCipher("test-secret-" * 4),
        max_log_bytes=1024 * 1024,
        domain_functions=domains,
    )


async def test_start_r_recusa_dominio_que_nao_existe_no_env(
    repository: SQLAlchemyJobRepository,
) -> None:
    intake = _intake(repository, ("acessos", "servidores", "roteamento"))
    autor = await _usuario(repository, "op-servidores", RoleLevel.SENIOR, "servidores")

    with pytest.raises(ValidationError) as erro:
        await intake.enqueue(
            context_for(autor),
            "dominio-inexistente",
            "docker ps",
            domain_function="redes",
        )

    # A mensagem precisa dizer o que existe; "inválido" sozinho não ajuda.
    assert "check the role" in str(erro.value)
    assert "acessos, servidores, roteamento" in str(erro.value)


async def test_start_r_nao_deixa_senior_publicar_fora_do_proprio_dominio(
    repository: SQLAlchemyJobRepository,
) -> None:
    intake = _intake(repository, ("acessos", "servidores"))
    senior = await _usuario(repository, "senior-redes", RoleLevel.SENIOR, "servidores")

    # "acessos" existe, mas não é o escopo dele: o domínio é autoridade,
    # não preferência.
    with pytest.raises(ForbiddenError):
        await intake.enqueue(
            context_for(senior),
            "fora-do-escopo",
            "docker ps",
            domain_function="acessos",
        )


async def test_start_r_admin_cruza_dominios_e_o_artefato_segue_o_pedido(
    repository: SQLAlchemyJobRepository,
    tmp_path: Path,
) -> None:
    intake = _intake(repository, ("acessos", "servidores"))
    admin = await _usuario(repository, "admin-global", RoleLevel.ADMIN, "plataforma")

    job = await intake.enqueue(
        context_for(admin), "cruza-dominio", "docker ps", domain_function="acessos"
    )
    assert job.domain_function == "acessos"

    processor = UploadProcessor(
        repository,
        AESGCMUploadCipher("test-secret-" * 4),
        StaticExtractor(),
        StaticTagInferrer(),
        StaticSecretScanner(),
        lease_seconds=30,
        retry_base_seconds=1,
        max_attempts=1,
    )
    await processor.process_once()

    service = JobService(
        repository, StaticSecretScanner(), LocalProvider(tmp_path / "playbooks")
    )
    published, _ = await service.publish(
        context_for(admin),
        job.id,
        "### Passo 1: Listar containers\n```bash\ndocker ps\n```\n",
        "publica-cruzando-dominio",
    )

    # O diretorio segue o `-r`, nao o dominio do autor (plataforma).
    assert published.storage_url is not None
    assert published.storage_url.startswith(
        f"local://{published.created_at.year}/acessos/"
    )
    arquivos = list((tmp_path / "playbooks").rglob("*.md"))
    assert len(arquivos) == 1
    assert arquivos[0].parent.name == "acessos"
    # E o frontmatter confiavel registra o mesmo dominio.
    assert 'funcao: "acessos"' in arquivos[0].read_text(encoding="utf-8")


async def test_sem_r_o_dominio_continua_sendo_o_do_autor(
    repository: SQLAlchemyJobRepository,
) -> None:
    intake = _intake(repository, ("acessos", "servidores"))
    autor = await _usuario(repository, "op-padrao", RoleLevel.SENIOR, "servidores")

    job = await intake.enqueue(context_for(autor), "sem-flag", "docker ps")

    # None e a forma de dizer "o do autor", resolvido na publicacao.
    assert job.domain_function is None


async def _com_areas(repository, username, role, primaria, extras=()):
    token_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    user = await repository.create_user(username, token_hash, role, primaria)
    if extras:
        user = await repository.update_user_scopes(user.id, None, None, extras)
    return user


async def test_senior_publica_em_area_adicional_concedida(
    repository: SQLAlchemyJobRepository,
) -> None:
    """O caso que motivou a mudança: um operador atende mais de uma área."""

    intake = _intake(repository, ("acessos", "servidores"))
    operador = await _com_areas(
        repository, "op-duas-areas", RoleLevel.SENIOR, "servidores", ("acessos",)
    )

    job = await intake.enqueue(
        context_for(operador), "na-area-extra", "docker ps", domain_function="acessos"
    )
    assert job.domain_function == "acessos"

    # A primária continua sendo o padrão sem `-r`.
    padrao = await intake.enqueue(context_for(operador), "sem-flag", "docker ps")
    assert padrao.domain_function is None
    assert operador.domain_function == "servidores"


async def test_area_nao_concedida_continua_recusada(
    repository: SQLAlchemyJobRepository,
) -> None:
    intake = _intake(repository, ("acessos", "servidores", "roteamento"))
    operador = await _com_areas(
        repository, "op-sem-roteamento", RoleLevel.SENIOR, "servidores", ("acessos",)
    )

    with pytest.raises(ForbiddenError) as erro:
        await intake.enqueue(
            context_for(operador),
            "area-nao-concedida",
            "docker ps",
            domain_function="roteamento",
        )
    # A mensagem lista o que ele tem, não só o que faltou.
    assert "acessos, servidores" in str(erro.value)


async def test_area_adicional_precisa_existir_no_env(
    repository: SQLAlchemyJobRepository,
) -> None:
    """Conceder área fora da lista criaria um diretório nunca declarado."""

    identity = IdentityService(
        repository, "pepper-de-teste" * 4, ("acessos", "servidores")
    )
    admin = await _com_areas(repository, "admin-areas", RoleLevel.ADMIN, "plataforma")
    alvo = await _com_areas(repository, "alvo-areas", RoleLevel.SENIOR, "servidores")

    with pytest.raises(ValidationError):
        await identity.update_scopes(
            context_for(admin), alvo.id, None, None, ("inexistente",)
        )


async def test_lista_de_areas_substitui_o_conjunto(
    repository: SQLAlchemyJobRepository,
) -> None:
    """`-r` reescreve o conjunto: revogar uma área é omiti-la."""

    identity = IdentityService(
        repository, "pepper-de-teste" * 4, ("acessos", "servidores", "roteamento")
    )
    admin = await _com_areas(repository, "admin-troca", RoleLevel.ADMIN, "plataforma")
    alvo = await _com_areas(
        repository, "alvo-troca", RoleLevel.SENIOR, "servidores", ("acessos",)
    )

    atualizado = await identity.update_scopes(
        context_for(admin), alvo.id, None, "servidores", ("roteamento",)
    )

    assert atualizado.extra_domains == ("roteamento",)
    assert atualizado.authorized_domains == {"servidores", "roteamento"}
    # `acessos` saiu porque não foi repetida na lista.
    assert "acessos" not in atualizado.authorized_domains


def test_subtitulo_livre_e_aceito_fora_dos_passos() -> None:
    """O CLI escreve o título do objetivo a partir de `lucien start -d`."""

    validado = validate_playbook(
        "## Objetivo\n\n"
        "### Comandos para verificação de rota down nas OLT's ZTE\n\n"
        "> **REVISÃO OBRIGATÓRIA — DESCRIÇÃO DO OPERADOR:** contexto.\n\n"
        "## Procedimento\n\n"
        "### Passo 1: Executar comando selecionado\n"
        "```bash\n"
        "show ip route\n"
        "```\n"
    )

    # O subtítulo não vira passo: só o comando real foi extraído.
    assert validado.command_blocks == ("show ip route",)


def test_subtitulo_livre_nao_pode_carregar_comando() -> None:
    """A brecha que a relaxação poderia abrir, fechada explicitamente.

    Se um `###` qualquer pudesse preceder um bloco bash, um comando entraria
    no runbook sem passar pela numeração sequencial de passos.
    """

    with pytest.raises(ValidationError) as erro:
        validate_playbook(
            "## Objetivo\n\n"
            "### Titulo qualquer\n"
            "```bash\n"
            "rm -rf /\n"
            "```\n"
        )
    assert "step heading" in str(erro.value)


def test_passo_com_numeracao_fora_de_ordem_continua_recusado() -> None:
    with pytest.raises(ValidationError):
        validate_playbook(
            "## Procedimento\n\n"
            "### Passo 1: Um\n```bash\necho um\n```\n\n"
            "### Passo 3: Tres\n```bash\necho tres\n```\n"
        )


def test_bloco_bash_orfao_continua_recusado() -> None:
    with pytest.raises(ValidationError):
        validate_playbook(
            "## Objetivo\n\nTexto.\n\n"
            "```bash\n"
            "curl http://interno/segredo\n"
            "```\n"
        )


def test_desafio_de_mfa_e_redigido() -> None:
    """Uma saída sintética de MFA entra no log como saída do acesso.

    Ela não é passo do procedimento e pode carregar dado pessoal: mesmo um
    telefone mascarado pode identificar a pessoa pelos últimos dígitos.
    """

    resultado = sanitize_secrets(
        "RADIUS challenge: Duo two-factor login for U000004\n"
        "Enter a passcode or select one of the following options:\n"
        " 1. Duo Push to +XX XX XXXXX-5081\n"
        " 2. SMS passcodes to +55 21 99999-1234\n"
        "Passcode or option (1-1):\n"
    )

    assert "U000004" not in resultado.text
    assert "5081" not in resultado.text
    assert "99999-1234" not in resultado.text
    assert "SEU_DESAFIO_MFA_AQUI" in resultado.text
    assert resultado.text.count("SEU_FATOR_MFA_AQUI") == 2
    # O recuo das opções sobrevive: o log precisa continuar legível.
    assert " 1. Duo Push to SEU_FATOR_MFA_AQUI" in resultado.text
    assert resultado.replacements >= 3


def test_telefone_mascarado_solto_tambem_e_redigido() -> None:
    resultado = sanitize_secrets("Contato de plantao: +55 21 98888-7766 (NOC)")

    assert "98888-7766" not in resultado.text
    assert "SEU_TELEFONE_AQUI" in resultado.text


def test_regra_de_mfa_nao_atinge_texto_operacional() -> None:
    """Uma regra ampla demais mutila o runbook sem ganho de segurança."""

    operacional = (
        "### Passo 1: Validar rota\n"
        "```bash\n"
        "ip route show | grep default\n"
        "```\n"
        "> Push to production somente apos aprovacao.\n"
        "Provedor Exemplo oferece autenticação multifator.\n"
        "O intervalo 1.5-2.0 segundos e aceitavel.\n"
        "Interface eth0 to eth1 espelhada.\n"
    )

    resultado = sanitize_secrets(operacional)

    assert resultado.text == operacional
    assert resultado.replacements == 0


def test_chave_de_segredo_nao_atravessa_quebra_de_linha() -> None:
    r"""`\s` no separador comia a primeira palavra da linha seguinte.

    Isso e sistematico em captura de terminal, onde `Password:` e sempre
    seguido de outra linha. O efeito era corromper comandos reais do runbook,
    trocando o primeiro token por um placeholder.
    """

    for linha_seguinte in [
        "ip route show",
        "systemctl restart frr",
        "display ont info 0 1",
    ]:
        original = f"Password:\n{linha_seguinte}\n"
        resultado = sanitize_secrets(original)
        assert resultado.text == original, original
        assert resultado.replacements == 0


def test_segredo_na_mesma_linha_continua_redigido() -> None:
    """A restricao do separador nao pode custar a deteccao que importa."""

    casos = {
        "PASSWORD=segredo123": "PASSWORD=SUA_SENHA_AQUI",
        "DB_PASSWORD: minhasenha": "DB_PASSWORD: SUA_SENHA_AQUI",
        "api_token = abc123xyz": "api_token = SEU_TOKEN_AQUI",
        'REDIS_PASSWORD:"outra"': 'REDIS_PASSWORD:"SUA_SENHA_REDIS_AQUI"',
        '{"token": "valor"}': '{"token": "SEU_TOKEN_AQUI"}',
        # A regra estruturada casa antes da literal do AKIA, entao o
        # placeholder e o generico. Comportamento anterior a esta mudanca.
        "ACCESS_KEY   =   AKIA0000000000000000": "ACCESS_KEY   =   SUA_KEY_AQUI",
    }
    for entrada, esperado in casos.items():
        resultado = sanitize_secrets(entrada)
        assert resultado.text == esperado, entrada
        assert resultado.replacements >= 1, entrada


def test_autor_combina_username_e_nome_completo() -> None:
    """O runbook publicado precisa ser legivel sem perder rastreabilidade."""

    identidade = PublicationIdentity(
        username="U000004",
        role_level=RoleLevel.SENIOR,
        domain_function="servidores",
        display_name="Operador Exemplo de Demonstracao Júnior",
    )

    assert identidade.author_label == "U000004 - Operador Exemplo de Demonstracao Júnior"


def test_autor_cai_para_o_username_sem_nome_completo() -> None:
    """Usuario criado pelo admin, ou LDAP sem o campo, nao pode virar 'None'."""

    identidade = PublicationIdentity(
        username="U000004",
        role_level=RoleLevel.SENIOR,
        domain_function="servidores",
    )

    assert identidade.author_label == "U000004"


def test_nome_completo_e_saneado_antes_de_publicar() -> None:
    """O valor vem do script do jump e acaba num YAML publicado."""

    from app.application import _normalize_display_name

    assert _normalize_display_name(None) is None
    assert _normalize_display_name("   ") is None
    # Espacos colapsados e quebras removidas: o frontmatter e YAML.
    assert (
        _normalize_display_name("Operador   Exemplo\tde  Demonstracao")
        == "Operador Exemplo de Demonstracao"
    )
    # Caractere de controle nao sobrevive.
    assert _normalize_display_name("Operador\x07Exemplo") == "OperadorExemplo"
    with pytest.raises(ValidationError):
        _normalize_display_name("x" * 121)


def test_frontmatter_com_nome_completo_continua_yaml_valido() -> None:
    """Aspas e dois-pontos no nome nao podem quebrar o frontmatter."""

    identidade = PublicationIdentity(
        username="U000004",
        role_level=RoleLevel.SENIOR,
        domain_function="servidores",
        display_name='Operador "Junior" de Demonstracao: o operador',
    )
    job = Job(
        id="3e381ebe-0284-4d3b-b304-a13655e3dd4c",
        owner_id="dono",
        name="teste",
        status=JobStatus.PENDING,
        commands=("ip addr",),
        command_outputs=("",),
        runbook_suggestions=RunbookSuggestions("", (), ("",), ()),
        inferred_tags=("rede",),
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    validado = validate_playbook(
        "### Passo 1: Inspecionar\n```bash\nip addr\n```\n"
    )

    documento = build_frontmatter(job, identidade, validado)
    cabecalho = documento.split("---\n")[1]

    # Uma unica linha `autor:`, com o valor inteiro escapado como escalar JSON.
    linhas_autor = [
        linha for linha in cabecalho.splitlines() if linha.startswith("autor:")
    ]
    assert len(linhas_autor) == 1
    assert linhas_autor[0].startswith('autor: "U000004 - Operador ')
    assert '\\"Junior\\"' in linhas_autor[0]
    # E o resto do cabecalho nao foi deslocado por injecao.
    assert 'nivel_autor: "senior"' in cabecalho


async def test_nome_do_ldap_chega_ao_frontmatter_publicado(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    """Cadeia completa: enrollment -> usuario -> contexto -> artefato."""

    identity = IdentityService(repository, "pepper-de-teste" * 4)

    # Primeiro login: cria a identidade ja com o nome vindo do GECOS.
    usuario, _, _, _ = await identity.enroll_jump_user(
        "U000004",
        "servidores",
        "idem-primeiro-login",
        "Operador Exemplo de Demonstracao Júnior",
    )
    assert usuario.display_name == "Operador Exemplo de Demonstracao Júnior"

    scanner = StaticSecretScanner()
    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    job = await ready_job(
        repository, usuario.id, "com-nome", "ip addr", scanner=scanner
    )
    publicado, _ = await service.publish(
        context_for(usuario),
        job.id,
        "### Passo 1: Inspecionar\n```bash\nip addr\n```\n",
        "publica-com-nome-0001",
    )

    arquivo = next((tmp_path / "playbooks").rglob("*.md"))
    conteudo = arquivo.read_text(encoding="utf-8")
    assert 'autor: "U000004 - Operador Exemplo de Demonstracao Júnior"' in conteudo
    assert publicado.status.value == "PUBLISHED"


async def test_troca_de_nome_no_ldap_propaga_no_proximo_login(
    repository: SQLAlchemyJobRepository,
) -> None:
    """O enrollment roda a cada login; o nome precisa acompanhar."""

    identity = IdentityService(repository, "pepper-de-teste" * 4)
    await identity.enroll_jump_user(
        "U000004", "servidores", "idem-login-1", "Operador B de Demonstracao"
    )

    atualizado, _, _, _ = await identity.enroll_jump_user(
        "U000004", None, "idem-login-2", "Operador Exemplo de Demonstracao Júnior"
    )

    assert atualizado.display_name == "Operador Exemplo de Demonstracao Júnior"


async def test_enrollment_sem_nome_preserva_o_que_ja_havia(
    repository: SQLAlchemyJobRepository,
) -> None:
    """Um jump server com script antigo nao pode apagar o nome ja gravado."""

    identity = IdentityService(repository, "pepper-de-teste" * 4)
    await identity.enroll_jump_user(
        "U000004", "servidores", "idem-com-nome", "Operador Exemplo"
    )

    depois, _, _, _ = await identity.enroll_jump_user(
        "U000004", None, "idem-sem-nome", None
    )

    assert depois.display_name == "Operador Exemplo"


async def test_nome_sobrevive_ao_redirecionamento_por_area(
    repository: SQLAlchemyJobRepository, tmp_path: Path
) -> None:
    """`lucien start -r` reconstrói a identidade; o nome não pode cair fora."""

    identity = IdentityService(repository, "pepper-de-teste" * 4)
    usuario, _, _, _ = await identity.enroll_jump_user(
        "U000004", "servidores", "idem-redirect", "Operador Exemplo de Demonstracao"
    )
    admin = await repository.create_user(
        "admin-redirect", "d" * 64, RoleLevel.ADMIN, "plataforma"
    )
    await repository.update_user_scopes(usuario.id, RoleLevel.SENIOR, None, ("acessos",))
    usuario = await repository.get_user(usuario.id)

    scanner = StaticSecretScanner()
    intake = UploadService(
        repository,
        scanner,
        AESGCMUploadCipher("test-secret-" * 4),
        max_log_bytes=1024 * 1024,
        domain_functions=("acessos", "servidores"),
    )
    job = await intake.enqueue(
        context_for(usuario), "com-r", "$ ip addr\n", domain_function="acessos"
    )
    processor = UploadProcessor(
        repository,
        AESGCMUploadCipher("test-secret-" * 4),
        StaticExtractor(),
        StaticTagInferrer(),
        scanner,
        lease_seconds=30,
        retry_base_seconds=1,
        max_attempts=1,
    )
    await processor.process_once()

    service = JobService(repository, scanner, LocalProvider(tmp_path / "playbooks"))
    await service.publish(
        context_for(usuario),
        job.id,
        "### Passo 1: Inspecionar\n```bash\nip addr\n```\n",
        "publica-redirect-0001",
    )

    arquivo = next((tmp_path / "playbooks").rglob("*.md"))
    conteudo = arquivo.read_text(encoding="utf-8")
    assert 'autor: "U000004 - Operador Exemplo de Demonstracao"' in conteudo
    assert 'funcao: "acessos"' in conteudo
    assert admin.id  # o admin existe apenas para documentar a concessao


def test_leitura_cobre_as_tres_geracoes_de_layout() -> None:
    """Artefato publicado é imutável: cada geração fica onde foi gravada."""

    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    job_id = "12345678-1234-1234-1234-123456789abc"

    atual = playbook_relative_path(job_id, created_at, domain_function="servidores")
    legados = legacy_playbook_relative_paths(
        job_id, created_at, domain_function="servidores"
    )

    assert atual.as_posix() == f"2026/servidores/{job_id}.md"
    assert [caminho.as_posix() for caminho in legados] == [
        f"servidores/2026/{job_id}.md",
        f"2026/07/{job_id}.md",
    ]
    # Mesmo arquivo em todas: só o diretório muda.
    assert {caminho.name for caminho in (atual, *legados)} == {f"{job_id}.md"}


async def test_local_le_publicacao_do_layout_por_mes(tmp_path: Path) -> None:
    """A geração mais antiga, de quando a publicação não congelava o domínio."""

    provider = LocalProvider(tmp_path / "playbooks")
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    job_id = "12345678-1234-1234-1234-123456789abc"

    antigo = tmp_path / "playbooks" / "2026" / "07"
    antigo.mkdir(parents=True)
    (antigo / f"{job_id}.md").write_text("### Passo 1\n", encoding="utf-8")

    conteudo = await provider.read_published(
        job_id, created_at, domain_function="servidores"
    )

    assert conteudo == "### Passo 1\n"


async def test_leitura_ausente_em_todos_os_layouts_responde_404(
    tmp_path: Path,
) -> None:
    provider = LocalProvider(tmp_path / "playbooks")

    with pytest.raises(NotFoundError):
        await provider.read_published(
            "12345678-1234-1234-1234-123456789abc",
            datetime(2026, 7, 16, tzinfo=UTC),
            domain_function="servidores",
        )


def test_identidade_incompleta_na_reserva_falha_alto() -> None:
    """Reserva corrompida não pode virar publicação silenciosamente errada.

    Sem username o frontmatter sairia com autor vazio; sem domínio o artefato
    iria para o diretório errado. Só `display_name` pode faltar -- publicações
    anteriores a essa coluna não têm a chave.
    """

    from app.infrastructure.database import _identity_from_payload

    completo = {
        "username": "U000004",
        "role_level": "senior",
        "domain_function": "servidores",
        "display_name": "Operador Exemplo de Demonstracao",
    }
    identidade = _identity_from_payload(completo)
    assert identidade is not None
    assert identidade.author_label == "U000004 - Operador Exemplo de Demonstracao"

    # Sem a coluna nova, o autor cai para o username.
    sem_nome = {chave: valor for chave, valor in completo.items() if chave != "display_name"}
    antiga = _identity_from_payload(sem_nome)
    assert antiga is not None
    assert antiga.author_label == "U000004"

    assert _identity_from_payload(None) is None

    for ausente in ("username", "role_level", "domain_function"):
        corrompido = {**completo, ausente: None}
        with pytest.raises(ConflictError):
            _identity_from_payload(corrompido)
