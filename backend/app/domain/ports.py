from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import (
    Job,
    PublicationIdentity,
    PublishedArtifact,
    QueuedUpload,
    RevisionSource,
    RoleLevel,
    RunbookEnrichment,
    RunbookSuggestions,
    SealedUpload,
    User,
)


class DomainError(Exception):
    """Erro esperado, seguro para conversão em resposta HTTP."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class AuthenticationError(DomainError):
    pass


class PreconditionFailedError(DomainError):
    pass


class ForbiddenError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class UpstreamError(DomainError):
    pass


class SecretDetectedError(ValidationError):
    pass


class IdentityRepository(ABC):
    @abstractmethod
    async def rotate_service_credential(
        self, name: str, scope: str, token_hash: str
    ) -> None:
        """Substitui atomicamente uma credencial M2M de escopo único."""

    @abstractmethod
    async def has_service_credential(self, token_hash: str, scope: str) -> bool:
        """Valida apenas o HMAC e o escopo; o token bruto nunca é persistido."""

    @abstractmethod
    async def create_bootstrap_admin(
        self,
        username: str,
        api_token_hash: str,
        domain_function: str,
    ) -> User:
        """Cria o primeiro admin de forma atômica e fecha o bootstrap."""

    @abstractmethod
    async def create_user(
        self,
        username: str,
        api_token_hash: str,
        role_level: RoleLevel,
        domain_function: str,
    ) -> User: ...

    @abstractmethod
    async def create_provisioned_user(
        self,
        username: str,
        provisional_token_hash: str,
        provisional_expires_at: datetime,
        role_level: RoleLevel,
        domain_function: str,
        extra_domains: tuple[str, ...] = (),
        display_name: str | None = None,
    ) -> User:
        """Cria usuário sem credencial permanente e com ativação temporária."""

    @abstractmethod
    async def find_user_by_token_hash(self, api_token_hash: str) -> User | None: ...

    @abstractmethod
    async def get_user(self, user_id: str) -> User: ...

    @abstractmethod
    async def get_user_by_identifier(self, id_or_username: str) -> User:
        """Localiza uma identidade administrativa por UUID ou username exato."""

    @abstractmethod
    async def issue_provisional_token(
        self,
        user_id: str,
        provisional_token_hash: str,
        provisional_expires_at: datetime,
        display_name: str | None = None,
    ) -> User:
        """Invalida a credencial atual e instala uma ativação temporária."""

    @abstractmethod
    async def exchange_provisional_token(
        self,
        provisional_token_hash: str,
        api_token_hash: str,
        idempotency_key_hash: str,
        exchanged_at: datetime,
    ) -> User:
        """Ativa uma vez e reconcilia retries da mesma operação atomicamente."""

    @abstractmethod
    async def update_user_scopes(
        self,
        user_id: str,
        role_level: RoleLevel | None,
        domain_function: str | None,
        extra_domains: tuple[str, ...] | None = None,
    ) -> User: ...

    @abstractmethod
    async def revoke_user(self, user_id: str) -> None: ...

    @abstractmethod
    async def count_active_admins(self) -> int: ...


class JobRepository(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def ping(self) -> None:
        """Levanta se o banco não responder. Sustenta a prontidão do Hub."""

    @abstractmethod
    async def operational_counters(self) -> dict[str, float]:
        """Contadores para diagnóstico: jobs por estado e estado da fila."""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def create_job(
        self,
        owner_id: str,
        name: str,
        commands: tuple[str, ...],
        inferred_tags: tuple[str, ...],
    ) -> Job: ...

    @abstractmethod
    async def enqueue_job(
        self,
        owner_id: str,
        name: str,
        fingerprint: str,
        ciphertext: str,
        skip_enrichment: bool = False,
        domain_function: str | None = None,
    ) -> Job:
        """Cria ou reconcilia um upload durável pelo nome e fingerprint."""

    @abstractmethod
    async def claim_next_upload(
        self, now: datetime, lease_until: datetime
    ) -> QueuedUpload | None:
        """Reserva um upload disponível sem bloquear outros workers."""

    @abstractmethod
    async def complete_upload(
        self,
        job_id: str,
        commands: tuple[str, ...],
        command_outputs: tuple[str, ...],
        runbook_suggestions: RunbookSuggestions,
        inferred_tags: tuple[str, ...],
        description: str = "",
    ) -> Job: ...

    @abstractmethod
    async def reschedule_upload(
        self, job_id: str, available_at: datetime
    ) -> bool:
        """Reagenda e informa se o Job ainda existia."""

    @abstractmethod
    async def fail_upload(self, job_id: str, error_code: str) -> bool:
        """Marca falha e informa se o Job ainda existia."""

    @abstractmethod
    async def retry_failed_upload(
        self,
        owner_id: str,
        id_or_name: str,
        available_at: datetime,
        skip_enrichment: bool | None = None,
    ) -> Job: ...

    @abstractmethod
    async def list_pending(self, owner_id: str) -> list[Job]: ...

    @abstractmethod
    async def list_active(self, owner_id: str) -> list[Job]:
        """Lista a fila operacional do proprietário, sem Jobs publicados."""

    @abstractmethod
    async def get_job(self, owner_id: str, id_or_name: str) -> Job: ...

    @abstractmethod
    async def get_published_for_revision(self, job_id: str) -> RevisionSource:
        """Obtém a versão e a identidade imutável da publicação raiz."""

    @abstractmethod
    async def list_published_runbook_ids(self, max_ids: int) -> tuple[str, ...]:
        """Lista IDs publicados que podem integrar o catálogo somente leitura."""

    @abstractmethod
    async def reserve_publication(
        self,
        owner_id: str,
        id_or_name: str,
        content_hash: str,
        idempotency_key: str,
        publication_identity: PublicationIdentity,
    ) -> Job: ...

    @abstractmethod
    async def mark_published(
        self,
        owner_id: str,
        job_id: str,
        storage_url: str,
        content_hash: str,
        idempotency_key: str,
    ) -> Job: ...

    @abstractmethod
    async def reserve_revision(
        self,
        owner_id: str,
        source_job_id: str,
        expected_content_hash: str,
        content_hash: str,
        idempotency_key: str,
        publication_identity: PublicationIdentity,
        commands: tuple[str, ...],
        stale_before: datetime,
    ) -> Job:
        """Cria ou recupera o único sucessor imutável de uma versão publicada."""

    @abstractmethod
    async def mark_revision_published(
        self,
        owner_id: str,
        revision_job_id: str,
        storage_url: str,
        content_hash: str,
        idempotency_key: str,
    ) -> Job: ...

    @abstractmethod
    async def delete_job(
        self, owner_id: str, id_or_name: str, force: bool = False
    ) -> Job:
        """Expurga Job próprio; force inclui PROCESSING, nunca PUBLISHED."""


class CommandExtractor(ABC):
    @abstractmethod
    async def extract(
        self, sanitized_log: str, sanitized_description: str | None = None
    ) -> tuple[str, ...]: ...


class RunbookEnricher(ABC):
    @abstractmethod
    async def infer(
        self,
        commands: tuple[str, ...],
        sanitized_description: str | None = None,
    ) -> RunbookEnrichment: ...


class SecretScanner(ABC):
    """Porta independente da DLP para detecção mandatória de segredos."""

    @abstractmethod
    async def detect(self, content: str) -> bool: ...


class UploadCipher(ABC):
    """Cifra o payload transitório e produz fingerprint não reversível."""

    @abstractmethod
    def seal(
        self,
        owner_id: str,
        name: str,
        sanitized_log: str,
        description: str | None,
    ) -> SealedUpload: ...

    @abstractmethod
    def open(
        self, owner_id: str, name: str, ciphertext: str
    ) -> tuple[str, str | None]: ...


class StorageProvider(ABC):
    """Strategy de destino; publicar o mesmo conteúdo deve ser idempotente."""

    async def aclose(self) -> None:
        """Libera conexões de rede. Destinos em disco não têm o que fechar."""

    @abstractmethod
    async def publish(
        self,
        job_id: str,
        created_at: datetime,
        markdown: str,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> PublishedArtifact: ...

    @abstractmethod
    async def read_published(
        self,
        job_id: str,
        created_at: datetime,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> str:
        """Devolve o Markdown publicado, incluindo o frontmatter gravado.

        O artefato e a fonte de verdade do conteudo: o Hub persiste apenas o
        hash. Sem esta leitura nao ha como abrir uma revisao fora do modo
        local, onde o portal alcanca o volume diretamente.
        """
        ...
