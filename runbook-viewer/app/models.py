from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Identidade obtida exclusivamente do endpoint confiável do Hub."""

    id: str
    username: str
    role_level: str
    # Area primaria. A autorizacao considera esta mais `extra_domains`.
    domain_function: str
    extra_domains: tuple[str, ...] = ()
    # Nome vindo do LDAP, so exibicao: nenhuma decisao o consulta.
    display_name: str | None = None

    @property
    def authorized_domains(self) -> frozenset[str]:
        return frozenset({self.domain_function, *self.extra_domains})


@dataclass(frozen=True, slots=True)
class RunbookSummary:
    id: str
    root_id: str
    revision: int
    replaces: str | None
    title: str
    author: str
    author_level: str
    domain_function: str
    root_domain_function: str
    created_at: datetime
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunbookDocument:
    summary: RunbookSummary
    html: str
    markdown: str
    body_hash: str
