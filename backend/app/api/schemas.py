from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models import Job, JobStatus, RoleLevel, RunbookSuggestions, User


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BootstrapAdminRequest(StrictRequest):
    username: str = Field(
        min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    domain_function: str = Field(
        default="plataforma",
        min_length=3,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class AdminCreateUserRequest(StrictRequest):
    username: str = Field(
        min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    role_level: RoleLevel
    domain_function: str = Field(
        min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"
    )
    # Areas alem da primaria. Lista completa, nao incremento.
    extra_domains: list[str] = Field(default_factory=list, max_length=32)


class AdminUpdateUserRequest(StrictRequest):
    role_level: RoleLevel | None = None
    domain_function: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"
    )
    # `None` preserva as areas atuais; `[]` revoga todas as adicionais.
    extra_domains: list[str] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def require_change(self) -> "AdminUpdateUserRequest":
        if (
            self.role_level is None
            and self.domain_function is None
            and self.extra_domains is None
        ):
            raise ValueError(
                "informe role_level, domain_function ou extra_domains"
            )
        return self


class ExchangeTokenRequest(StrictRequest):
    """Corpo vazio e estrito; a credencial permanece no header Authorization."""


class JumpEnrollRequest(StrictRequest):
    username: str = Field(
        min_length=2, max_length=64, pattern=r"^[A-Za-z][0-9]+$"
    )
    # A lista valida vem de RUNBOOK_DOMAIN_FUNCTIONS, nao do schema: aqui so a
    # gramatica. IdentityService recusa o que nao estiver configurado.
    domain_function: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"
    )
    # Nome completo do LDAP, so exibicao. O saneamento fica no servico.
    display_name: str | None = Field(default=None, max_length=120)


class ProvisionalTokenRequest(StrictRequest):
    # Ausente/vazio preserva o comportamento de sempre (grava em
    # api_token_hash). Um nome isola a emissão naquele escopo -- por
    # exemplo, dar a alguém uma chave "personal" sem mexer na "jump".
    scope: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"
    )


class UserResponse(BaseModel):
    id: str
    username: str
    role_level: RoleLevel
    domain_function: str
    is_active: bool
    extra_domains: list[str] = Field(default_factory=list)
    display_name: str | None = None

    @classmethod
    def from_domain(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            username=user.username,
            role_level=user.role_level,
            domain_function=user.domain_function,
            is_active=user.is_active,
            extra_domains=list(user.extra_domains),
            display_name=user.display_name,
        )


class IssuedUserResponse(UserResponse):
    api_token: str

    @classmethod
    def from_issued(cls, user: User, api_token: str) -> "IssuedUserResponse":
        return cls(
            id=user.id,
            username=user.username,
            role_level=user.role_level,
            domain_function=user.domain_function,
            is_active=user.is_active,
            extra_domains=list(user.extra_domains),
            display_name=user.display_name,
            api_token=api_token,
        )


class ProvisionedUserResponse(UserResponse):
    provisional_token: str
    expires_at: datetime
    # Presente só na primeira vez que esta identidade ganha uma credencial
    # permanente pessoal (fluxo do jump). O aviso de "só aparece uma vez"
    # é responsabilidade de quem exibe -- o script do jump, não esta API.
    personal_token: str | None = None

    @classmethod
    def from_provisioned(
        cls,
        user: User,
        provisional_token: str,
        expires_at: datetime,
        personal_token: str | None = None,
    ) -> "ProvisionedUserResponse":
        return cls(
            id=user.id,
            username=user.username,
            role_level=user.role_level,
            domain_function=user.domain_function,
            is_active=user.is_active,
            extra_domains=list(user.extra_domains),
            display_name=user.display_name,
            provisional_token=provisional_token,
            expires_at=expires_at,
            personal_token=personal_token,
        )


class UploadRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    # Vazio é válido: um runbook puramente visual (só cliques numa interface)
    # não tem sessão de terminal para capturar. Nesse caso o worker pula a
    # extração de comandos em vez de tratar "nada extraído" como falha.
    raw_log: str = Field(default="")
    description: str | None = Field(default=None, max_length=280)
    skip_enrichment: bool = False
    # `lucien start -r`. Ausente significa "o dominio do autor"; o Hub decide.
    domain_function: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"
    )

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class PublishedContentResponse(BaseModel):
    """Corpo revisavel e o hash que o cliente devolve em If-Match."""

    markdown: str
    content_hash: str


class RetryRequest(StrictRequest):
    """Corpo opcional do retry; `None` preserva a escolha do upload original."""

    skip_enrichment: bool | None = None


class RunbookAssetInput(StrictRequest):
    """Uma imagem anexada, antes do gate de segurança (OCR + gitleaks).

    O teto real de tamanho/dimensão é decidido em runtime por settings, dentro
    do `TesseractImageScanner` -- o `max_length` abaixo é só uma barreira
    barata contra payload absurdo antes de qualquer decode.
    """

    filename: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]{1,128}$"
    )
    content_base64: str = Field(min_length=1, max_length=28_000_000)
    media_type: Literal["image/png", "image/jpeg"]


class PublishRequest(StrictRequest):
    markdown: str = Field(min_length=1, max_length=1024 * 1024)
    assets: list[RunbookAssetInput] = Field(default_factory=list)


class RevisionRequest(StrictRequest):
    markdown: str = Field(min_length=1, max_length=1024 * 1024)
    assets: list[RunbookAssetInput] = Field(default_factory=list)


class PublishedRunbookCatalogResponse(BaseModel):
    ids: list[str] = Field(max_length=10_000)
    # Aditivo: id -> nome, so preenchido pelas rotas que ja tem o nome a mao
    # (hoje, /runbooks/published/mine). Quem so le `ids` nao percebe a mudanca.
    names: dict[str, str] = Field(default_factory=dict)


class RunbookConfigurationResponse(BaseModel):
    language: Literal["pt-br", "en"]
    # Valores aceitos em `lucien start -r`, para o CLI mostrar o que existe.
    domain_functions: list[str] = Field(default_factory=list)


class RunbookSuggestionsResponse(BaseModel):
    objective: str
    architecture_prerequisites: list[str]
    command_impacts: list[str]
    rollback_commands: list[str]

    @classmethod
    def from_domain(
        cls, suggestions: RunbookSuggestions
    ) -> "RunbookSuggestionsResponse":
        return cls(
            objective=suggestions.objective,
            architecture_prerequisites=list(suggestions.architecture_prerequisites),
            command_impacts=list(suggestions.command_impacts),
            rollback_commands=list(suggestions.rollback_commands),
        )


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: JobStatus
    description: str = ""
    domain_function: str | None = None
    commands: list[str]
    command_outputs: list[str]
    runbook_suggestions: RunbookSuggestionsResponse
    inferred_tags: list[str]
    created_at: datetime
    storage_url: str | None = None
    processing_error: str | None = None

    @classmethod
    def from_domain(cls, job: Job) -> "JobResponse":
        return cls(
            id=job.id,
            name=job.name,
            status=job.status,
            description=job.description,
            domain_function=job.domain_function,
            commands=list(job.commands),
            command_outputs=list(job.command_outputs),
            runbook_suggestions=RunbookSuggestionsResponse.from_domain(
                job.runbook_suggestions
            ),
            inferred_tags=list(job.inferred_tags),
            created_at=job.created_at,
            storage_url=job.storage_url,
            processing_error=job.processing_error,
        )


class PublishResponse(JobResponse):
    sanitization_count: int = Field(ge=0)

    @classmethod
    def from_publication(
        cls, job: Job, sanitization_count: int
    ) -> "PublishResponse":
        return cls(
            id=job.id,
            name=job.name,
            status=job.status,
            description=job.description,
            domain_function=job.domain_function,
            commands=list(job.commands),
            command_outputs=list(job.command_outputs),
            runbook_suggestions=RunbookSuggestionsResponse.from_domain(
                job.runbook_suggestions
            ),
            inferred_tags=list(job.inferred_tags),
            created_at=job.created_at,
            storage_url=job.storage_url,
            processing_error=job.processing_error,
            sanitization_count=sanitization_count,
        )


class RevisionResponse(PublishResponse):
    root_job_id: str
    supersedes_job_id: str
    revision_number: int = Field(ge=2)

    @classmethod
    def from_publication(
        cls, job: Job, sanitization_count: int
    ) -> "RevisionResponse":
        if job.root_job_id is None or job.supersedes_job_id is None:
            raise RuntimeError("resposta de revisão sem linhagem")
        return cls(
            id=job.id,
            name=job.name,
            status=job.status,
            description=job.description,
            domain_function=job.domain_function,
            commands=list(job.commands),
            command_outputs=list(job.command_outputs),
            runbook_suggestions=RunbookSuggestionsResponse.from_domain(
                job.runbook_suggestions
            ),
            inferred_tags=list(job.inferred_tags),
            created_at=job.created_at,
            storage_url=job.storage_url,
            sanitization_count=sanitization_count,
            root_job_id=job.root_job_id,
            supersedes_job_id=job.supersedes_job_id,
            revision_number=job.revision_number,
        )
