import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import Settings
from app.domain.correlation import (
    definir_correlacao,
    identificador_aceitavel,
    limpar_correlacao,
)
from app.domain.credentials import digest_api_token
from app.domain.models import RoleLevel, SecurityContext
from app.domain.ports import IdentityRepository

CABECALHO_CORRELACAO = "X-Request-Id"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Dá um identificador a cada requisição e o devolve na resposta.

    Roda por fora de tudo: erro recusado na autenticação também precisa de
    identificador, senão o caso mais difícil de investigar é justamente o que
    fica sem rastro.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        identificador = identificador_aceitavel(
            request.headers.get(CABECALHO_CORRELACAO)
        )
        token = definir_correlacao(identificador)
        try:
            resposta = await call_next(request)
        finally:
            limpar_correlacao(token)
        resposta.headers[CABECALHO_CORRELACAO] = identificador
        return resposta


__all__ = [
    "CABECALHO_CORRELACAO",
    "CorrelationMiddleware",
    "RequestSizeMiddleware",
    "SecurityMiddleware",
    "digest_api_token",
    "require_admin",
    "require_security_context",
]


def require_security_context(request: Request) -> SecurityContext:
    context = getattr(request.state, "security_context", None)
    if not isinstance(context, SecurityContext):
        raise HTTPException(status_code=401, detail="credencial inválida")
    return context


def require_admin(
    context: Annotated[SecurityContext, Depends(require_security_context)],
) -> SecurityContext:
    if context.role_level is not RoleLevel.ADMIN:
        raise HTTPException(status_code=403, detail="operação exclusiva de admin")
    return context


class SecurityMiddleware(BaseHTTPMiddleware):
    """Aplica TLS obrigatório e autenticação Bearer antes das rotas."""

    def __init__(
        self, app: object, settings: Settings, repository: IdentityRepository
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings
        self._repository = repository

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # O Hub termina TLS diretamente; não confiamos em X-Forwarded-Proto arbitrário.
        if request.url.scheme != "https" and not self._settings.allow_insecure_dev:
            return JSONResponse({"detail": "TLS obrigatório"}, status_code=400)

        # Vivacidade e prontidão ficam abertas: uma sonda não carrega
        # credencial, e as duas só revelam se o Hub consegue atender -- um bit
        # que já se obtém tentando usá-lo. `/metrics`, que descreve o ritmo da
        # operação, continua exigindo admin.
        if request.url.path in {"/health", "/ready"}:
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token:
            return JSONResponse({"detail": "credencial inválida"}, status_code=401)

        bootstrap = self._settings.bootstrap_api_key.get_secret_value()
        if request.url.path == "/bootstrap/admin" and request.method == "POST":
            if not self._settings.user_creation_enabled:
                return JSONResponse(
                    {"detail": "criação de usuário desabilitada"}, status_code=403
                )
            if not hmac.compare_digest(token, bootstrap):
                return JSONResponse({"detail": "credencial inválida"}, status_code=401)
            request.state.bootstrap_authorized = True
            return await call_next(request)

        if request.url.path == "/auth/exchange" and request.method == "POST":
            if not token.startswith("luc_tmp_"):
                return JSONResponse(
                    {"detail": "invalid provisional token"}, status_code=401
                )
            # A validação HMAC, expiração e consumo único acontecem atomicamente
            # no caso de uso e no repositório; o middleware nunca concede contexto.
            request.state.provisional_token = token
            return await call_next(request)

        if request.url.path == "/auth/jump/enroll" and request.method == "POST":
            if not token.startswith("luc_jump_"):
                return JSONResponse(
                    {"detail": "credencial técnica inválida"}, status_code=401
                )
            digest = digest_api_token(
                token, self._settings.auth_pepper.get_secret_value()
            )
            if not await self._repository.has_service_credential(
                digest, "jump_enrollment"
            ):
                return JSONResponse(
                    {"detail": "credencial técnica inválida"}, status_code=401
                )
            request.state.jump_enrollment_authorized = True
            return await call_next(request)

        digest = digest_api_token(
            token, self._settings.auth_pepper.get_secret_value()
        )
        user = await self._repository.find_user_by_token_hash(digest)
        if user is None or not user.is_active:
            return JSONResponse({"detail": "credencial inválida"}, status_code=401)

        request.state.security_context = SecurityContext.from_user(user)
        return await call_next(request)


class RequestSizeMiddleware(BaseHTTPMiddleware):
    """Rejeita corpos sem tamanho conhecido e evita buffering arbitrário."""

    def __init__(self, app: object, max_body_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_body_bytes = max_body_bytes

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in {"POST", "PUT", "PATCH"}:
            return await call_next(request)

        value = request.headers.get("Content-Length")
        if value is None:
            return JSONResponse({"detail": "Content-Length obrigatório"}, status_code=411)
        try:
            size = int(value)
        except ValueError:
            return JSONResponse({"detail": "Content-Length inválido"}, status_code=400)
        if size < 0 or size > self._max_body_bytes:
            return JSONResponse({"detail": "payload excede o limite"}, status_code=413)
        return await call_next(request)
