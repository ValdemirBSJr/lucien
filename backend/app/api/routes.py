import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, Response, status

from app.api.schemas import (
    AdminCreateUserRequest,
    AdminUpdateUserRequest,
    BootstrapAdminRequest,
    ExchangeTokenRequest,
    IssuedUserResponse,
    JobResponse,
    JumpEnrollRequest,
    ProvisionalTokenRequest,
    PublishedRunbookCatalogResponse,
    ProvisionedUserResponse,
    PublishRequest,
    PublishResponse,
    RevisionRequest,
    PublishedContentResponse,
    RetryRequest,
    RevisionResponse,
    RunbookAssetInput,
    RunbookConfigurationResponse,
    UploadRequest,
    UserResponse,
)
from app.application import IdentityService, JobService, UploadService
from app.domain.models import SecurityContext
from app.domain.ports import JobRepository, RawAssetInput
from app.infrastructure.security import require_admin, require_security_context


router = APIRouter()
_CANONICAL_JOB_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_QUOTED_CONTENT_HASH = re.compile(r'^"([0-9a-f]{64})"$')


def _service(request: Request) -> JobService:
    return request.app.state.job_service


def _to_raw_assets(assets: list[RunbookAssetInput]) -> tuple[RawAssetInput, ...]:
    """Converte o DTO da API para o tipo de dominio; a aplicacao nunca ve Pydantic."""

    return tuple(
        RawAssetInput(
            filename=asset.filename,
            content_base64=asset.content_base64,
            media_type=asset.media_type,
        )
        for asset in assets
    )


def _upload_service(request: Request) -> UploadService:
    return request.app.state.upload_service


def _identity_service(request: Request) -> IdentityService:
    return request.app.state.identity_service


def _disable_secret_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _validate_idempotency_key(value: str) -> None:
    if not 8 <= len(value) <= 128:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must be between 8 and 128 characters",
        )


def _parse_if_match(value: str) -> str:
    match = _QUOTED_CONTENT_HASH.fullmatch(value)
    if match is None:
        raise HTTPException(
            status_code=400,
            detail='If-Match must contain exactly "<sha256-of-the-body>"',
        )
    return match.group(1)


UserContext = Annotated[SecurityContext, Depends(require_security_context)]
AdminContext = Annotated[SecurityContext, Depends(require_admin)]


def _repository(request: Request) -> JobRepository:
    return request.app.state.repository


@router.get("/health")
async def health() -> dict[str, str]:
    """Vivacidade: o processo responde.

    Não consulta o banco de propósito. Se o PostgreSQL cair, reiniciar o Hub
    não resolve nada -- e é isso que um healthcheck reprovado provoca. Quem
    responde "consigo atender" é `/ready`.
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, str]:
    """Prontidão: o Hub alcança o que precisa para atender."""
    try:
        await _repository(request).ping()
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "indisponível", "database": "inalcançável"}
    return {"status": "pronto", "database": "ok"}


@router.get("/metrics")
async def metrics(_: AdminContext, request: Request) -> Response:
    """Contadores operacionais em formato de texto do Prometheus.

    Exige admin: profundidade de fila e volume de Jobs descrevem o ritmo da
    operação, e não há scraper nesta instalação para justificar deixá-los
    abertos. Serve ao operador com `curl`, e a um scraper autenticado se um
    dia houver.
    """
    contadores = await _repository(request).operational_counters()
    linhas = [
        f"lucien_{nome} {valor:g}" for nome, valor in sorted(contadores.items())
    ]
    return Response(
        "\n".join(linhas) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/bootstrap/admin",
    response_model=IssuedUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap_admin(
    payload: BootstrapAdminRequest, request: Request, response: Response
) -> IssuedUserResponse:
    if not getattr(request.state, "bootstrap_authorized", False):
        raise HTTPException(status_code=401, detail="invalid bootstrap credential")
    user, api_token = await _identity_service(request).bootstrap_admin(
        payload.username, payload.domain_function
    )
    _disable_secret_caching(response)
    return IssuedUserResponse.from_issued(user, api_token)


@router.get("/me", response_model=UserResponse)
async def me(context: UserContext, request: Request) -> UserResponse:
    user = await _identity_service(request).get_user(context)
    return UserResponse.from_domain(user)


@router.get("/configuration/runbook", response_model=RunbookConfigurationResponse)
async def runbook_configuration(
    request: Request, _context: UserContext
) -> RunbookConfigurationResponse:
    return RunbookConfigurationResponse(
        language=request.app.state.runbook_language,
        domain_functions=list(request.app.state.runbook_domain_functions),
    )


@router.post("/auth/exchange", response_model=IssuedUserResponse)
async def exchange_provisional_token(
    _payload: ExchangeTokenRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> IssuedUserResponse:
    _validate_idempotency_key(idempotency_key)
    provisional_token = getattr(request.state, "provisional_token", None)
    if not isinstance(provisional_token, str):
        raise HTTPException(status_code=401, detail="invalid provisional token")
    user, api_token = await _identity_service(request).exchange_provisional_token(
        provisional_token, idempotency_key
    )
    _disable_secret_caching(response)
    return IssuedUserResponse.from_issued(user, api_token)


@router.post("/auth/jump/enroll", response_model=ProvisionedUserResponse)
async def enroll_jump_user(
    payload: JumpEnrollRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ProvisionedUserResponse:
    _validate_idempotency_key(idempotency_key)
    if not getattr(request.state, "jump_enrollment_authorized", False):
        raise HTTPException(status_code=401, detail="invalid service credential")
    user, provisional_token, expires_at, personal_token = (
        await _identity_service(request).enroll_jump_user(
            payload.username,
            payload.domain_function,
            idempotency_key,
            payload.display_name,
        )
    )
    _disable_secret_caching(response)
    return ProvisionedUserResponse.from_provisioned(
        user, provisional_token, expires_at, personal_token
    )


@router.get("/runbooks/published", response_model=PublishedRunbookCatalogResponse)
async def list_published_runbooks(
    request: Request, response: Response, _context: UserContext
) -> PublishedRunbookCatalogResponse:
    identifiers = await _service(request).list_published_runbook_ids()
    _disable_secret_caching(response)
    return PublishedRunbookCatalogResponse(ids=list(identifiers))


@router.get(
    "/runbooks/published/mine", response_model=PublishedRunbookCatalogResponse
)
async def list_published_runbooks_mine(
    request: Request, response: Response, context: UserContext
) -> PublishedRunbookCatalogResponse:
    """So os IDs que `context` pode de fato revisar -- filtrado por area."""

    pares = await _service(request).list_published_runbooks_for(context)
    _disable_secret_caching(response)
    return PublishedRunbookCatalogResponse(
        ids=[id_ for id_, _ in pares], names={id_: nome for id_, nome in pares}
    )


@router.post(
    "/admin/users",
    response_model=ProvisionedUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_user(
    payload: AdminCreateUserRequest,
    request: Request,
    response: Response,
    context: AdminContext,
) -> ProvisionedUserResponse:
    user, provisional_token, expires_at = await _identity_service(request).create_user(
        context,
        payload.username,
        payload.role_level,
        payload.domain_function,
        tuple(payload.extra_domains),
    )
    _disable_secret_caching(response)
    return ProvisionedUserResponse.from_provisioned(
        user, provisional_token, expires_at
    )


@router.patch("/admin/users/{id_or_username}", response_model=UserResponse)
async def admin_update_user(
    id_or_username: str,
    payload: AdminUpdateUserRequest,
    request: Request,
    context: AdminContext,
) -> UserResponse:
    user = await _identity_service(request).update_scopes(
        context,
        id_or_username,
        payload.role_level,
        payload.domain_function,
        None if payload.extra_domains is None else tuple(payload.extra_domains),
    )
    return UserResponse.from_domain(user)


@router.post(
    "/admin/users/{id_or_username}/provisional-token",
    response_model=ProvisionedUserResponse,
)
async def admin_issue_provisional_token(
    id_or_username: str,
    request: Request,
    response: Response,
    context: AdminContext,
    payload: Annotated[ProvisionalTokenRequest | None, Body()] = None,
) -> ProvisionedUserResponse:
    scope = None if payload is None else payload.scope
    user, provisional_token, expires_at = (
        await _identity_service(request).issue_provisional_token(
            context, id_or_username, scope
        )
    )
    _disable_secret_caching(response)
    return ProvisionedUserResponse.from_provisioned(
        user, provisional_token, expires_at
    )


@router.delete(
    "/admin/users/{id_or_username}", status_code=status.HTTP_204_NO_CONTENT
)
async def admin_revoke_user(
    id_or_username: str, request: Request, context: AdminContext
) -> Response:
    await _identity_service(request).revoke_user(context, id_or_username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/admin/users/{id_or_username}/reinstate",
    response_model=ProvisionedUserResponse,
)
async def admin_reinstate_user(
    id_or_username: str,
    request: Request,
    response: Response,
    context: AdminContext,
) -> ProvisionedUserResponse:
    user, provisional_token, expires_at = (
        await _identity_service(request).reinstate_user(context, id_or_username)
    )
    _disable_secret_caching(response)
    return ProvisionedUserResponse.from_provisioned(
        user, provisional_token, expires_at
    )


@router.post("/upload", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload(
    payload: UploadRequest, request: Request, context: UserContext
) -> JobResponse:
    job = await _upload_service(request).enqueue(
        context,
        payload.name,
        payload.raw_log,
        payload.description,
        payload.skip_enrichment,
        payload.domain_function,
    )
    return JobResponse.from_domain(job)


@router.get("/jobs/pending", response_model=list[JobResponse])
async def list_pending(request: Request, context: UserContext) -> list[JobResponse]:
    jobs = await _service(request).list_pending(context.user_id)
    return [JobResponse.from_domain(job) for job in jobs]


@router.get("/jobs/active", response_model=list[JobResponse])
async def list_active(request: Request, context: UserContext) -> list[JobResponse]:
    jobs = await _service(request).list_active(context.user_id)
    return [JobResponse.from_domain(job) for job in jobs]


@router.get("/jobs/{id_or_name}", response_model=JobResponse)
async def get_job(
    id_or_name: str, request: Request, context: UserContext
) -> JobResponse:
    job = await _service(request).get_job(context.user_id, id_or_name)
    return JobResponse.from_domain(job)


@router.post("/jobs/{id_or_name}/retry", response_model=JobResponse)
async def retry_job(
    id_or_name: str,
    request: Request,
    context: UserContext,
    payload: RetryRequest | None = None,
) -> JobResponse:
    job = await _upload_service(request).retry(
        context.user_id,
        id_or_name,
        None if payload is None else payload.skip_enrichment,
    )
    return JobResponse.from_domain(job)


@router.post("/jobs/{id_or_name}/publish", response_model=PublishResponse)
async def publish_job(
    id_or_name: str,
    payload: PublishRequest,
    request: Request,
    context: UserContext,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> PublishResponse:
    _validate_idempotency_key(idempotency_key)
    job, sanitization_count = await _service(request).publish(
        context,
        id_or_name,
        payload.markdown,
        idempotency_key,
        _to_raw_assets(payload.assets),
    )
    return PublishResponse.from_publication(job, sanitization_count)


@router.get(
    "/runbooks/{current_job_id}/content",
    response_model=PublishedContentResponse,
)
async def published_runbook_content(
    current_job_id: Annotated[str, Path(pattern=_CANONICAL_JOB_ID)],
    request: Request,
    context: UserContext,
) -> PublishedContentResponse:
    """Entrega o corpo revisavel e o hash a ser devolvido em If-Match."""

    markdown, content_hash = await _service(request).published_content(
        context, current_job_id
    )
    return PublishedContentResponse(
        markdown=markdown, content_hash=content_hash
    )


@router.post(
    "/runbooks/{current_job_id}/revisions", response_model=RevisionResponse
)
async def revise_runbook(
    current_job_id: Annotated[str, Path(pattern=_CANONICAL_JOB_ID)],
    payload: RevisionRequest,
    request: Request,
    context: UserContext,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    if_match: Annotated[str, Header(alias="If-Match")],
) -> RevisionResponse:
    _validate_idempotency_key(idempotency_key)
    revision, sanitization_count = await _service(request).revise(
        context,
        current_job_id,
        _parse_if_match(if_match),
        payload.markdown,
        idempotency_key,
        _to_raw_assets(payload.assets),
    )
    return RevisionResponse.from_publication(revision, sanitization_count)


@router.delete("/jobs/{id_or_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    id_or_name: str,
    request: Request,
    context: UserContext,
    force: Annotated[bool, Query()] = False,
) -> Response:
    await _service(request).delete(context.user_id, id_or_name, force)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
