from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.application import IdentityService, JobService, UploadService
from app.config import Settings
from app.domain.ports import (
    AuthenticationError,
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
    UpstreamError,
    ValidationError,
)
from app.domain.correlation import correlacao_atual
from app.infrastructure.audit import configure_audit_logging
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.security import (
    CorrelationMiddleware,
    RequestSizeMiddleware,
    SecurityMiddleware,
)
from app.infrastructure.image_scanner import TesseractImageScanner
from app.infrastructure.secret_scanner import GitleaksSecretScanner
from app.infrastructure.storage import build_storage_provider
from app.infrastructure.upload_cipher import AESGCMUploadCipher


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_audit_logging()
    repository = SQLAlchemyJobRepository(settings.database_url)
    secret_scanner = GitleaksSecretScanner(
        settings.secret_scanner_url, settings.secret_scanner_timeout_seconds
    )
    cipher = AESGCMUploadCipher(settings.auth_pepper.get_secret_value())
    # O repositório também é o espelho: publicar passa a gravar o documento e
    # as imagens no banco, para que a árvore publicada possa ser reproduzida
    # sem o Git.
    storage = build_storage_provider(settings, mirror=repository)
    image_scanner = TesseractImageScanner(
        secret_scanner,
        settings.max_asset_bytes,
        settings.max_asset_dimension_px,
        settings.ocr_languages,
    )
    identity_service = IdentityService(
        repository,
        settings.auth_pepper.get_secret_value(),
        settings.domain_functions,
    )
    service = JobService(
        repository,
        secret_scanner,
        storage,
        image_scanner,
        # O mesmo `lucien runbook revise` vale em local, GitHub e Gitea: o
        # provider sabe ler e escrever o artefato, e o Hub mantem RBAC,
        # sanitizacao e linhagem identicos nos tres.
        revisions_enabled=True,
        entry_roles_enabled=settings.rbac_entry_roles_enabled,
        max_assets_per_publication=settings.max_assets_per_publication,
    )
    upload_service = UploadService(
        repository,
        secret_scanner,
        cipher,
        settings.max_log_bytes,
        settings.domain_functions,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await repository.initialize()
        try:
            yield
        finally:
            await secret_scanner.aclose()
            await storage.aclose()
            await repository.close()

    app = FastAPI(
        title="Runbook API Hub",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.identity_service = identity_service
    app.state.job_service = service
    app.state.upload_service = upload_service
    app.state.runbook_language = settings.slm_language_runbook
    app.state.runbook_domain_functions = settings.domain_functions
    app.add_middleware(
        SecurityMiddleware, settings=settings, repository=repository
    )
    app.add_middleware(
        RequestSizeMiddleware,
        max_body_bytes=max(settings.max_log_bytes, 1024 * 1024) + 128 * 1024,
    )
    # Por último na montagem é por fora na execução: o identificador precisa
    # existir antes de qualquer middleware que possa recusar a requisição.
    app.add_middleware(CorrelationMiddleware)
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, error: DomainError) -> JSONResponse:
        status_code = 400
        if isinstance(error, AuthenticationError):
            status_code = 401
        elif isinstance(error, NotFoundError):
            status_code = 404
        elif isinstance(error, ForbiddenError):
            status_code = 403
        elif isinstance(error, ValidationError):
            status_code = 422
        elif isinstance(error, ConflictError):
            status_code = 409
        elif isinstance(error, PreconditionFailedError):
            status_code = 412
        elif isinstance(error, UpstreamError):
            status_code = 502
        corpo: dict[str, str] = {"detail": str(error)}
        # O identificador vai no corpo, e não só no cabeçalho: quem relata um
        # erro copia o que está na tela.
        correlacao = correlacao_atual()
        if correlacao is not None:
            corpo["request_id"] = correlacao
        return JSONResponse(corpo, status_code=status_code)

    return app


app = create_app()
