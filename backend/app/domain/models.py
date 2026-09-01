from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    PROCESSING = "PROCESSING"
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class RoleLevel(StrEnum):
    JUNIOR = "junior"
    PLENO = "pleno"
    SENIOR = "senior"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    role_level: RoleLevel
    # Area primaria: o destino quando `lucien start` roda sem `-r`, e a que
    # aparece no frontmatter por padrao.
    domain_function: str
    is_active: bool = True
    # Nome completo vindo do LDAP, so para exibicao. `username` continua sendo a
    # identidade autoritativa: e por ele que auditoria e RBAC respondem.
    display_name: str | None = None
    # Areas adicionais concedidas pelo admin. Um operador costuma atender mais
    # de uma area, e trocar a primaria a cada demanda faria ele perder acesso
    # ao que ja publicou.
    extra_domains: tuple[str, ...] = ()

    @property
    def authorized_domains(self) -> frozenset[str]:
        return frozenset({self.domain_function, *self.extra_domains})


@dataclass(frozen=True, slots=True)
class SecurityContext:
    """Identidade autenticada criada exclusivamente pelo Hub."""

    user_id: str
    username: str
    role_level: RoleLevel
    domain_function: str
    extra_domains: tuple[str, ...] = ()
    display_name: str | None = None

    @classmethod
    def from_user(cls, user: User) -> "SecurityContext":
        return cls(
            user_id=user.id,
            username=user.username,
            role_level=user.role_level,
            domain_function=user.domain_function,
            extra_domains=user.extra_domains,
            display_name=user.display_name,
        )

    def authorizes(self, domain_function: str) -> bool:
        """Admin cruza qualquer area; os demais, apenas as concedidas."""

        if self.role_level is RoleLevel.ADMIN:
            return True
        return domain_function in {self.domain_function, *self.extra_domains}

    @property
    def authorized_domains(self) -> frozenset[str]:
        return frozenset({self.domain_function, *self.extra_domains})


# Lista historica, usada quando RUNBOOK_DOMAIN_FUNCTIONS nao e declarada.
# Manter o padrao igual ao que estava fixo no codigo evita que uma
# instalacao existente perca dominios ao atualizar.
DEFAULT_DOMAIN_FUNCTIONS: tuple[str, ...] = (
    "acessos",
    "servidores",
    "redes",
    "suporte",
)


@dataclass(frozen=True, slots=True)
class PublicationIdentity:
    username: str
    role_level: RoleLevel
    domain_function: str
    display_name: str | None = None

    @property
    def author_label(self) -> str:
        """O que aparece como autor no runbook publicado.

        Formato misto, `U000004 - Operador Exemplo de Demonstracao Junior`: o nome
        completo torna o documento legivel para quem le a wiki, e o username
        continua no mesmo campo porque e ele que a auditoria e o RBAC
        conhecem. Trocar um pelo outro perderia rastreabilidade.

        Cai para so o username quando nao ha nome completo -- usuario criado
        pelo admin, ou LDAP sem o campo preenchido.
        """

        if not self.display_name:
            return self.username
        return f"{self.username} - {self.display_name}"

    @classmethod
    def from_context(cls, context: SecurityContext) -> "PublicationIdentity":
        return cls(
            username=context.username,
            role_level=context.role_level,
            domain_function=context.domain_function,
            display_name=context.display_name,
        )


@dataclass(frozen=True, slots=True)
class RunbookSuggestions:
    """Conteúdo auxiliar da SLM; nunca representa uma decisão de segurança."""

    objective: str
    architecture_prerequisites: tuple[str, ...]
    command_impacts: tuple[str, ...]
    rollback_commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunbookEnrichment:
    inferred_tags: tuple[str, ...]
    suggestions: RunbookSuggestions


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    owner_id: str
    name: str
    status: JobStatus
    commands: tuple[str, ...]
    command_outputs: tuple[str, ...]
    runbook_suggestions: RunbookSuggestions
    inferred_tags: tuple[str, ...]
    created_at: datetime
    # Descrição sanitizada informada pelo operador em `lucien start -d`; é texto
    # do operador, nunca sugestão da SLM, e o CLI a rotula como tal.
    description: str = ""
    # Dominio pedido em `lucien start -r`. `None` significa "o do autor" e e
    # resolvido na publicacao; guardar a escolha aqui mantem o artefato no
    # diretorio decidido na captura, mesmo que o pedido demore a ser publicado.
    domain_function: str | None = None
    storage_url: str | None = None
    content_hash: str | None = None
    idempotency_key: str | None = None
    publication_identity: PublicationIdentity | None = None
    root_job_id: str | None = None
    supersedes_job_id: str | None = None
    revision_number: int = 1
    processing_error: str | None = None


@dataclass(frozen=True, slots=True)
class SealedUpload:
    ciphertext: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class QueuedUpload:
    job_id: str
    owner_id: str
    name: str
    ciphertext: str
    attempts: int
    # Opt-out explícito do operador (`--skip-enrichment`), independente de
    # SLM_ENRICHMENT_ENABLED; vale para este Job apenas.
    skip_enrichment: bool = False


@dataclass(frozen=True, slots=True)
class RevisionSource:
    """Versão editada e identidade imutável da publicação raiz."""

    job: Job
    root_identity: PublicationIdentity


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    url: str
