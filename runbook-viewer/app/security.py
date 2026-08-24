import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import AuthenticatedUser


_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ROLES = frozenset({"junior", "pleno", "senior", "admin"})


class InvalidCredentialsError(Exception):
    """Credencial ausente, expirada, revogada ou incompatível."""


class IdentityUnavailableError(Exception):
    """O Hub não pôde confirmar a identidade neste momento."""


class RevisionForbiddenError(Exception):
    """O Hub negou a criação da revisão para esta identidade."""


class RevisionConflictError(Exception):
    """O documento mudou ou a revisão conflita com outra publicação."""


class RevisionPreconditionFailedError(Exception):
    """A versão usada como base deixou de ser a versão atual."""


class RevisionRejectedError(Exception):
    """O Markdown não passou pela validação server-side."""


class IdentityVerifier(Protocol):
    async def verify(self, username: str, token: str) -> AuthenticatedUser:
        """Valida a credencial no provedor de identidade."""


class RevisionPublisher(Protocol):
    async def create_revision(
        self,
        current_job_id: str,
        markdown: str,
        body_hash: str,
        idempotency_key: str,
        token: str,
    ) -> None:
        """Solicita uma revisão sem escrever no volume local."""


class PublishedCatalogReader(Protocol):
    async def list_published_ids(self, token: str) -> frozenset[str]:
        """Obtém do Hub a lista confiável de artefatos publicados."""


class HubClientPort(
    IdentityVerifier, RevisionPublisher, PublishedCatalogReader, Protocol
):
    """Porta mínima usada pelo viewer para conversar com o Hub."""


class _MePayload(BaseModel):
    """Forma exata de `GET /me`.

    `extra="forbid"` fica de proposito. Foi ele que acusou a divergencia
    quando o Hub ganhou `display_name` e `extra_domains` sem que este lado
    fosse atualizado: um schema permissivo teria aceitado calado e a
    autorizacao de edicao ficaria errada em silencio.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    role_level: str
    domain_function: str
    is_active: bool
    extra_domains: list[str] = Field(default_factory=list)
    display_name: str | None = None


class _PublishedCatalogPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str]


class HubClient:
    """Adapter único para identidade e revisões no Runbook API Hub."""

    def __init__(self, client: httpx.AsyncClient, max_catalog_ids: int = 10_000) -> None:
        self._client = client
        self._max_catalog_ids = max_catalog_ids

    async def verify(self, username: str, token: str) -> AuthenticatedUser:
        if _USERNAME_PATTERN.fullmatch(username) is None or not token:
            raise InvalidCredentialsError
        try:
            response = await self._client.get(
                "/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise IdentityUnavailableError from error

        if response.status_code in {401, 403, 404}:
            raise InvalidCredentialsError
        if response.status_code != 200:
            raise IdentityUnavailableError
        try:
            payload = _MePayload.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise IdentityUnavailableError from error
        if (
            not payload.is_active
            or _USERNAME_PATTERN.fullmatch(payload.username) is None
            or not hmac.compare_digest(payload.username, username)
            or payload.role_level not in _ROLES
            or _DOMAIN_PATTERN.fullmatch(payload.domain_function) is None
            # Cada area adicional passa pela mesma gramatica da primaria: elas
            # decidem o que o operador pode editar.
            or any(
                _DOMAIN_PATTERN.fullmatch(dominio) is None
                for dominio in payload.extra_domains
            )
        ):
            raise InvalidCredentialsError
        return AuthenticatedUser(
            id=payload.id,
            username=payload.username,
            role_level=payload.role_level,
            domain_function=payload.domain_function,
            extra_domains=tuple(payload.extra_domains),
            display_name=payload.display_name,
        )

    async def list_published_ids(self, token: str) -> frozenset[str]:
        try:
            response = await self._client.get(
                "/runbooks/published",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise IdentityUnavailableError from error
        if response.status_code in {401, 403}:
            raise InvalidCredentialsError
        if response.status_code != 200:
            raise IdentityUnavailableError
        try:
            payload = _PublishedCatalogPayload.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise IdentityUnavailableError from error
        if len(payload.ids) > self._max_catalog_ids:
            raise IdentityUnavailableError
        identifiers = frozenset(payload.ids)
        if len(identifiers) != len(payload.ids) or any(
            not _is_canonical_uuid(identifier) for identifier in identifiers
        ):
            raise IdentityUnavailableError
        return identifiers

    async def create_revision(
        self,
        current_job_id: str,
        markdown: str,
        body_hash: str,
        idempotency_key: str,
        token: str,
    ) -> None:
        try:
            response = await self._client.post(
                f"/runbooks/{current_job_id}/revisions",
                json={"markdown": markdown},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": idempotency_key,
                    "If-Match": f'"{body_hash}"',
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise IdentityUnavailableError from error
        if response.status_code in {200, 201}:
            return
        if response.status_code == 401:
            raise InvalidCredentialsError
        if response.status_code == 403:
            raise RevisionForbiddenError
        if response.status_code == 412:
            raise RevisionPreconditionFailedError
        if response.status_code in {404, 409}:
            raise RevisionConflictError
        if response.status_code == 422:
            raise RevisionRejectedError
        raise IdentityUnavailableError


# Mantém o nome usado por integrações da primeira versão do viewer.
HubIdentityVerifier = HubClient


@dataclass(frozen=True, slots=True)
class SessionCredential:
    username: str
    token: str


class SessionCipher:
    """Mantém a credencial cifrada e autenticada no cookie do navegador."""

    def __init__(self, secret: str, ttl_seconds: int) -> None:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)
        self._ttl_seconds = ttl_seconds

    def seal(self, credential: SessionCredential) -> str:
        payload = json.dumps(
            {"username": credential.username, "token": credential.token},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def open(self, value: str) -> SessionCredential:
        try:
            raw = self._fernet.decrypt(
                value.encode("ascii"), ttl=self._ttl_seconds
            )
            payload = json.loads(raw.decode("utf-8"))
            username = payload["username"]
            token = payload["token"]
        except (InvalidToken, UnicodeError, ValueError, KeyError, TypeError):
            raise InvalidCredentialsError from None
        if (
            not isinstance(username, str)
            or _USERNAME_PATTERN.fullmatch(username) is None
            or not isinstance(token, str)
            or not 16 <= len(token) <= 512
        ):
            raise InvalidCredentialsError
        return SessionCredential(username=username, token=token)


@dataclass(frozen=True, slots=True)
class EditFormState:
    root_id: str
    current_job_id: str
    body_hash: str
    csrf_token: str
    idempotency_key: str


class EditFormCipher:
    """Protege concorrência, CSRF e idempotência contra alteração no formulário."""

    def __init__(self, secret: str, ttl_seconds: int = 600) -> None:
        material = f"lucien-viewer-edit-v1\0{secret}".encode()
        key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        self._fernet = Fernet(key)
        self._ttl_seconds = ttl_seconds

    def seal(self, state: EditFormState) -> str:
        payload = json.dumps(
            {
                "root_id": state.root_id,
                "current_job_id": state.current_job_id,
                "body_hash": state.body_hash,
                "csrf_token": state.csrf_token,
                "idempotency_key": state.idempotency_key,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def open(self, value: str) -> EditFormState:
        try:
            payload = json.loads(
                self._fernet.decrypt(
                    value.encode("ascii"), ttl=self._ttl_seconds
                ).decode("utf-8")
            )
            state = EditFormState(
                root_id=payload["root_id"],
                current_job_id=payload["current_job_id"],
                body_hash=payload["body_hash"],
                csrf_token=payload["csrf_token"],
                idempotency_key=payload["idempotency_key"],
            )
        except (InvalidToken, UnicodeError, ValueError, KeyError, TypeError):
            raise InvalidCredentialsError from None
        if (
            not _is_canonical_uuid(state.root_id)
            or not _is_canonical_uuid(state.current_job_id)
            or re.fullmatch(r"[0-9a-f]{64}", state.body_hash) is None
            or not 32 <= len(state.csrf_token) <= 128
            or re.fullmatch(r"revision-[0-9a-f-]{36}", state.idempotency_key) is None
        ):
            raise InvalidCredentialsError
        return state


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False
