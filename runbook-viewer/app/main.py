import hmac
import re
import secrets
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.models import AuthenticatedUser, RunbookDocument, RunbookSummary
from app.repository import CatalogLimitError, RunbookRepository
from app.security import (
    EditFormCipher,
    EditFormState,
    HubClient,
    HubClientPort,
    IdentityUnavailableError,
    InvalidCredentialsError,
    RevisionConflictError,
    RevisionForbiddenError,
    RevisionPreconditionFailedError,
    RevisionRejectedError,
    SessionCipher,
    SessionCredential,
)


SESSION_COOKIE = "__Host-lucien_viewer"
CSRF_COOKIE = "__Host-lucien_csrf"
EDIT_CSRF_COOKIE = "__Host-lucien_edit_csrf"
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_PAGE_SIZE = 50
_BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class _AuthenticatedSession:
    user: AuthenticatedUser
    credential: SessionCredential


def create_app(
    settings: Settings | None = None,
    hub_client: HubClientPort | None = None,
    repository: RunbookRepository | None = None,
) -> FastAPI:
    """Composition root com fronteiras injetáveis para testes."""

    settings = settings or Settings()  # type: ignore[call-arg]
    owned_client: httpx.AsyncClient | None = None
    if hub_client is None:
        trust_context = ssl.create_default_context()
        trust_context.load_verify_locations(cafile=str(settings.viewer_hub_ca_file))
        owned_client = httpx.AsyncClient(
            base_url=settings.viewer_hub_url,
            verify=trust_context,
            timeout=httpx.Timeout(5.0, connect=2.0, pool=2.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        hub_client = HubClient(owned_client, settings.viewer_max_documents)
    catalog = repository or RunbookRepository(
        settings.viewer_runbooks_root,
        settings.viewer_max_documents,
        settings.viewer_max_file_bytes,
    )
    cipher = SessionCipher(
        settings.viewer_session_secret.get_secret_value(),
        settings.viewer_session_ttl_seconds,
    )
    edit_cipher = EditFormCipher(settings.viewer_session_secret.get_secret_value())
    templates = Jinja2Templates(directory=_BASE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if owned_client is not None:
                await owned_client.aclose()

    app = FastAPI(
        title="Lucien Runbooks",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: object) -> Response:
        request_limit: int | None = None
        if request.method == "POST" and request.url.path.endswith("/edit"):
            # application/x-www-form-urlencoded pode ocupar até três bytes para
            # cada byte do Markdown, além do estado cifrado do formulário.
            request_limit = settings.viewer_max_file_bytes * 3 + 131_072
        elif request.method == "POST" and request.url.path == "/login":
            request_limit = 16 * 1024
        elif request.method == "POST" and request.url.path == "/logout":
            request_limit = 1024

        early_response: Response | None = None
        if request_limit is not None:
            content_length = request.headers.get("Content-Length")
            if content_length is None:
                early_response = JSONResponse(
                    {"detail": "Content-Length obrigatório"}, status_code=411
                )
            else:
                try:
                    request_size = int(content_length)
                except ValueError:
                    early_response = JSONResponse(
                        {"detail": "Content-Length inválido"}, status_code=400
                    )
                else:
                    if request_size < 0 or request_size > request_limit:
                        early_response = JSONResponse(
                            {"detail": "payload excede o limite"}, status_code=413
                        )
        response = early_response or await call_next(request)  # type: ignore[operator]
        if request.url.path.startswith("/static/"):
            # Assets não contêm dados de usuário; cache evita baixar novamente o logo.
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'; connect-src 'none'; "
            "img-src 'self' data:; font-src 'self'; style-src 'self'; "
            "script-src 'self'"
        )
        return response

    async def authenticated_session(request: Request) -> _AuthenticatedSession:
        encrypted = request.cookies.get(SESSION_COOKIE, "")
        if not encrypted:
            raise InvalidCredentialsError
        credential = cipher.open(encrypted)
        assert hub_client is not None
        user = await hub_client.verify(
            credential.username, credential.token
        )
        return _AuthenticatedSession(user=user, credential=credential)

    async def published_ids(session: _AuthenticatedSession) -> frozenset[str]:
        assert hub_client is not None
        return await hub_client.list_published_ids(session.credential.token)

    def login_page(
        request: Request,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        csrf_token = secrets.token_urlsafe(32)
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf_token, "error": error},
            status_code=status_code,
        )
        _set_cookie(response, CSRF_COOKIE, csrf_token, max_age=300)
        return response

    @app.exception_handler(InvalidCredentialsError)
    async def invalid_credentials(_: Request, __: InvalidCredentialsError) -> Response:
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, samesite="strict")
        return response

    @app.exception_handler(IdentityUnavailableError)
    async def identity_unavailable(
        request: Request, _: IdentityUnavailableError
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Hub temporariamente indisponível",
                "message": "Não foi possível confirmar sua sessão. Tente novamente em instantes.",
            },
            status_code=503,
        )

    @app.exception_handler(CatalogLimitError)
    async def catalog_limit(request: Request, _: CatalogLimitError) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Catálogo temporariamente indisponível",
                "message": "O limite operacional de documentos foi atingido.",
            },
            status_code=503,
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/login", response_class=HTMLResponse)
    async def login(request: Request) -> HTMLResponse:
        return login_page(request)

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: Annotated[str, Form()],
        api_token: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
        if (
            not csrf_cookie
            or not csrf_token
            or not hmac.compare_digest(csrf_cookie, csrf_token)
        ):
            return login_page(
                request, "A sessão do formulário expirou. Tente novamente.", 400
            )
        if (
            _USERNAME_PATTERN.fullmatch(username) is None
            or not 16 <= len(api_token) <= 512
        ):
            return login_page(request, "Usuário ou token inválido.", 401)
        assert hub_client is not None
        try:
            user = await hub_client.verify(username, api_token)
        except InvalidCredentialsError:
            return login_page(request, "Usuário ou token inválido.", 401)
        except IdentityUnavailableError:
            raise
        encrypted = cipher.seal(
            SessionCredential(username=user.username, token=api_token)
        )
        response = RedirectResponse(url="/", status_code=303)
        _set_cookie(
            response,
            SESSION_COOKIE,
            encrypted,
            max_age=settings.viewer_session_ttl_seconds,
        )
        response.delete_cookie(CSRF_COOKIE, path="/", secure=True, samesite="strict")
        return response

    @app.post("/logout")
    async def logout() -> Response:
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, samesite="strict")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        categoria: str | None = Query(default=None, max_length=64),
        pagina: int = Query(default=1, ge=1, le=100_000),
    ) -> HTMLResponse:
        session = await authenticated_session(request)
        summaries = await catalog.list_runbooks(await published_ids(session))
        categories = _group_categories(summaries)
        selected = categoria if categoria in categories else None
        filtered = categories[selected] if selected else summaries
        total_pages = max(1, (len(filtered) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if pagina > total_pages:
            raise HTTPException(status_code=404, detail="página inexistente")
        start = (pagina - 1) * _PAGE_SIZE
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "user": session.user,
                "runbooks": filtered[start : start + _PAGE_SIZE],
                "categories": categories,
                "selected_category": selected,
                "page": pagina,
                "total_pages": total_pages,
                "total_documents": len(summaries),
            },
        )

    @app.get("/runbooks/{runbook_id}", response_class=HTMLResponse)
    async def runbook_detail(request: Request, runbook_id: str) -> HTMLResponse:
        session = await authenticated_session(request)
        allowed_ids = await published_ids(session)
        document = await catalog.get_runbook(runbook_id, allowed_ids)
        if document is None:
            raise HTTPException(status_code=404, detail="runbook não encontrado")
        summaries = await catalog.list_runbooks(allowed_ids)
        return templates.TemplateResponse(
            request=request,
            name="runbook.html",
            context={
                "user": session.user,
                "document": document,
                "categories": _group_categories(summaries),
                "can_edit": _can_edit(
                    session.user,
                    document.summary.root_domain_function,
                    settings.rbac_entry_roles_enabled,
                ),
            },
        )

    @app.get("/runbooks/{root_id}/edit", response_class=HTMLResponse)
    async def edit_runbook(request: Request, root_id: str) -> HTMLResponse:
        session = await authenticated_session(request)
        document = await catalog.get_runbook(
            root_id, await published_ids(session)
        )
        if document is None:
            raise HTTPException(status_code=404, detail="runbook não encontrado")
        if not _can_edit(
            session.user,
            document.summary.root_domain_function,
            settings.rbac_entry_roles_enabled,
        ):
            raise HTTPException(status_code=403, detail="edição não permitida")

        csrf_token = secrets.token_urlsafe(32)
        state = EditFormState(
            root_id=document.summary.root_id,
            current_job_id=document.summary.id,
            body_hash=document.body_hash,
            csrf_token=csrf_token,
            idempotency_key=f"revision-{uuid4()}",
        )
        response = _render_edit_page(
            templates,
            request,
            session.user,
            document,
            edit_cipher.seal(state),
            document.markdown,
        )
        _set_cookie(response, EDIT_CSRF_COOKIE, csrf_token, max_age=600)
        return response

    @app.post("/runbooks/{root_id}/edit", response_class=HTMLResponse)
    async def submit_runbook_edit(
        request: Request,
        root_id: str,
        markdown: Annotated[str, Form()],
        edit_state: Annotated[str, Form()],
    ) -> Response:
        session = await authenticated_session(request)
        try:
            state = edit_cipher.open(edit_state)
        except InvalidCredentialsError:
            raise HTTPException(status_code=400, detail="formulário inválido") from None
        csrf_cookie = request.cookies.get(EDIT_CSRF_COOKIE, "")
        if (
            state.root_id != root_id
            or not csrf_cookie
            or not hmac.compare_digest(csrf_cookie, state.csrf_token)
        ):
            raise HTTPException(status_code=400, detail="formulário inválido")

        document = await catalog.get_runbook(
            root_id, await published_ids(session)
        )
        if document is None:
            raise HTTPException(status_code=404, detail="runbook não encontrado")
        if not _can_edit(
            session.user,
            document.summary.root_domain_function,
            settings.rbac_entry_roles_enabled,
        ):
            raise HTTPException(status_code=403, detail="edição não permitida")
        if not markdown.strip() or len(markdown.encode("utf-8")) > settings.viewer_max_file_bytes:
            return _render_edit_page(
                templates,
                request,
                session.user,
                document,
                edit_state,
                markdown,
                "O conteúdo deve ter entre 1 byte e o limite configurado.",
                422,
            )

        assert hub_client is not None
        try:
            await hub_client.create_revision(
                state.current_job_id,
                markdown,
                state.body_hash,
                state.idempotency_key,
                session.credential.token,
            )
        except RevisionForbiddenError:
            raise HTTPException(status_code=403, detail="edição não permitida") from None
        except RevisionConflictError:
            return _render_edit_page(
                templates,
                request,
                session.user,
                document,
                edit_state,
                markdown,
                "O runbook mudou desde a abertura. Recarregue antes de editar novamente.",
                409,
            )
        except RevisionPreconditionFailedError:
            return _render_edit_page(
                templates,
                request,
                session.user,
                document,
                edit_state,
                markdown,
                "O runbook mudou desde a abertura. Recarregue antes de editar novamente.",
                412,
            )
        except RevisionRejectedError:
            return _render_edit_page(
                templates,
                request,
                session.user,
                document,
                edit_state,
                markdown,
                "O Hub recusou o Markdown. Revise a estrutura e tente novamente.",
                422,
            )
        except IdentityUnavailableError:
            return _render_edit_page(
                templates,
                request,
                session.user,
                document,
                edit_state,
                markdown,
                "O Hub está indisponível. Tente novamente nesta página para reutilizar a mesma chave idempotente.",
                503,
            )

        catalog.invalidate()
        response = RedirectResponse(url=f"/runbooks/{state.root_id}", status_code=303)
        response.delete_cookie(
            EDIT_CSRF_COOKIE, path="/", secure=True, samesite="strict"
        )
        return response

    return app


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _group_categories(
    summaries: tuple[RunbookSummary, ...],
) -> dict[str, tuple[RunbookSummary, ...]]:
    grouped: dict[str, list[RunbookSummary]] = {}
    for summary in summaries:
        grouped.setdefault(summary.root_domain_function, []).append(summary)
    return {
        name: tuple(grouped[name])
        for name in sorted(grouped, key=lambda value: value.casefold())
    }


def _can_edit(
    user: AuthenticatedUser, domain_function: str, entry_roles_enabled: bool = False
) -> bool:
    # A decisão final continua no Hub; esta regra controla somente a apresentação.
    if user.role_level == "admin":
        return True
    allowed = {"senior", "junior", "pleno"} if entry_roles_enabled else {"senior"}
    # Um operador pode atender mais de uma área. Comparar só com a primária
    # esconderia o botão de quem o Hub autorizaria — e o inverso, mostrar para
    # quem não pode, apenas leva a um 403 do Hub.
    return user.role_level in allowed and domain_function in user.authorized_domains


def _render_edit_page(
    templates: Jinja2Templates,
    request: Request,
    user: AuthenticatedUser,
    document: RunbookDocument,
    edit_state: str,
    markdown: str,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="edit.html",
        context={
            "user": user,
            "document": document,
            "edit_state": edit_state,
            "markdown": markdown,
            "error": error,
        },
        status_code=status_code,
    )
