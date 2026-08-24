from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
import pytest

from app.config import Settings
from app.domain.models import RoleLevel, User
from app.domain.credentials import digest_api_token
from app.infrastructure.security import SecurityMiddleware


class LookupRepository:
    def __init__(self, expected_digest: str, active: bool = True) -> None:
        self.expected_digest = expected_digest
        self.active = active

    async def find_user_by_token_hash(self, api_token_hash: str) -> User | None:
        if api_token_hash == self.expected_digest:
            return User(
                id="user-1",
                username="alice",
                role_level=RoleLevel.JUNIOR,
                domain_function="servidores",
                is_active=self.active,
            )
        return None


def build_app(allow_insecure: bool = False, active: bool = True) -> FastAPI:
    token = "luc_token-valido"
    pepper = "p" * 32
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        bootstrap_api_key="b" * 32,
        auth_pepper=pepper,
        allow_insecure_dev=allow_insecure,
    )
    app = FastAPI()
    app.add_middleware(
        SecurityMiddleware,
        settings=settings,
        repository=LookupRepository(digest_api_token(token, pepper), active=active),
    )

    @app.get("/private")
    async def private(request: Request) -> dict[str, str]:
        return {"user_id": request.state.security_context.user_id}

    return app


def test_tls_e_bearer_sao_obrigatorios() -> None:
    app = build_app()
    with TestClient(app, base_url="http://testserver") as insecure:
        assert insecure.get("/private").status_code == 400

    with TestClient(app, base_url="https://testserver") as secure:
        assert secure.get("/private").status_code == 401
        assert secure.get(
            "/private", headers={"Authorization": "Bearer luc_token-valido"}
        ).json() == {"user_id": "user-1"}


def test_bootstrap_nao_autentica_como_usuario() -> None:
    app = build_app()
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(
            "/private", headers={"Authorization": f"Bearer {'b' * 32}"}
        )
        assert response.status_code == 401


def test_usuario_revogado_perde_acesso_imediatamente() -> None:
    app = build_app(active=False)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(
            "/private", headers={"Authorization": "Bearer luc_token-valido"}
        )
        assert response.status_code == 401


def test_api_git_recusa_transporte_sem_tls() -> None:
    with pytest.raises(PydanticValidationError, match="GIT_API_BASE"):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            bootstrap_api_key="b" * 32,
            auth_pepper="p" * 32,
            git_api_base="http://gitea.exemplo.interno/api/v1",
        )
