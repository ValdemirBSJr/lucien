import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.domain.credentials import digest_api_token


@pytest.mark.asyncio
async def test_rotacao_da_credencial_m2m_invalida_a_anterior(tmp_path: Path) -> None:
    from app.infrastructure.database import SQLAlchemyJobRepository

    repository = SQLAlchemyJobRepository(
        f"sqlite+aiosqlite:///{(tmp_path / 'service-credential.db').as_posix()}"
    )
    await repository.initialize()
    try:
        await repository.rotate_service_credential(
            "jump-server", "jump_enrollment", "hash-anterior"
        )
        assert await repository.has_service_credential(
            "hash-anterior", "jump_enrollment"
        )
        await repository.rotate_service_credential(
            "jump-server", "jump_enrollment", "hash-novo"
        )
        assert not await repository.has_service_credential(
            "hash-anterior", "jump_enrollment"
        )
        assert await repository.has_service_credential(
            "hash-novo", "jump_enrollment"
        )
    finally:
        await repository.close()


def test_admin_gerencia_escopos_e_revogacao_e_imediata(
    tmp_path: Path, monkeypatch
) -> None:
    # app.main mantém a instância ASGI de produção; fornecemos ambiente só ao import.
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "b" * 32)
    monkeypatch.setenv("AUTH_PEPPER", "p" * 32)
    from app.main import create_app
    from app.infrastructure.secret_scanner import GitleaksSecretScanner

    async def allow_content(_: GitleaksSecretScanner, _content: str) -> bool:
        return False

    monkeypatch.setattr(GitleaksSecretScanner, "detect", allow_content)

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'iam.db').as_posix()}",
        bootstrap_api_key="b" * 32,
        auth_pepper="p" * 32,
        slm_language_runbook="en",
        user_creation_enabled=True,
        allow_insecure_dev=True,
        local_storage_root=tmp_path / "playbooks",
    )
    app = create_app(settings)

    with TestClient(app, base_url="http://testserver") as client:
        bootstrap = client.post(
            "/bootstrap/admin",
            headers={"Authorization": f"Bearer {'b' * 32}"},
            json={"username": "root-admin", "domain_function": "plataforma"},
        )
        assert bootstrap.status_code == 201
        assert bootstrap.headers["cache-control"] == "no-store"
        admin_token = bootstrap.json()["api_token"]

        created = client.post(
            "/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "operador-jr",
                "role_level": "junior",
                "domain_function": "servidores",
            },
        )
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        junior = created.json()

        provisional_token = junior["provisional_token"]
        expires_at = datetime.fromisoformat(junior["expires_at"])
        remaining = expires_at - datetime.now(UTC)
        assert provisional_token.startswith("luc_tmp_")
        assert timedelta(hours=3, minutes=59) < remaining <= timedelta(hours=4)
        assert (
            client.get(
                "/me",
                headers={"Authorization": f"Bearer {provisional_token}"},
            ).status_code
            == 401
        )

        exchange = client.post(
            "/auth/exchange",
            headers={
                "Authorization": f"Bearer {provisional_token}",
                "Idempotency-Key": "exchange-junior-001",
            },
            json={},
        )
        assert exchange.status_code == 200
        assert exchange.headers["cache-control"] == "no-store"
        permanent_token = exchange.json()["api_token"]
        assert permanent_token.startswith("luc_")
        assert not permanent_token.startswith("luc_tmp_")
        retry = client.post(
            "/auth/exchange",
            headers={
                "Authorization": f"Bearer {provisional_token}",
                "Idempotency-Key": "exchange-junior-001",
            },
            json={},
        )
        assert retry.status_code == 200
        assert retry.json()["api_token"] == permanent_token
        assert client.post(
            "/auth/exchange",
            headers={
                "Authorization": f"Bearer {provisional_token}",
                "Idempotency-Key": "exchange-junior-different",
            },
            json={},
        ).status_code == 401

        junior_headers = {"Authorization": f"Bearer {permanent_token}"}
        assert client.get("/me", headers=junior_headers).json()["role_level"] == "junior"
        assert (
            client.post(
                "/admin/users",
                headers=junior_headers,
                json={
                    "username": "tentativa",
                    "role_level": "admin",
                    "domain_function": "plataforma",
                },
            ).status_code
            == 403
        )

        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        configuration = client.get(
            "/configuration/runbook", headers=admin_headers
        )
        assert configuration.status_code == 200
        assert configuration.json() == {
            "language": "en",
            # Lista que autoriza `lucien start -r`; este e o padrao usado
            # quando RUNBOOK_DOMAIN_FUNCTIONS nao e declarada.
            "domain_functions": ["acessos", "servidores", "redes", "suporte"],
        }
        assert client.get("/configuration/runbook").status_code == 401
        accepted = client.post(
            "/upload",
            headers=admin_headers,
            json={"name": "http-async", "raw_log": "docker ps"},
        )
        assert accepted.status_code == 202
        assert accepted.json()["status"] == "PROCESSING"
        assert client.get("/jobs/pending", headers=admin_headers).json() == []
        active_jobs = client.get("/jobs/active", headers=admin_headers)
        assert active_jobs.status_code == 200
        assert [job["id"] for job in active_jobs.json()] == [accepted.json()["id"]]
        assert active_jobs.json()[0]["status"] == "PROCESSING"
        assert client.get(
            f"/jobs/{accepted.json()['id']}", headers=admin_headers
        ).json()["status"] == "PROCESSING"
        assert client.delete(
            f"/jobs/{accepted.json()['id']}", headers=admin_headers
        ).status_code == 409
        assert client.delete(
            f"/jobs/{accepted.json()['id']}?force=true", headers=admin_headers
        ).status_code == 204
        assert client.get(
            f"/jobs/{accepted.json()['id']}", headers=admin_headers
        ).status_code == 404

        catalog = client.get("/runbooks/published", headers=admin_headers)
        assert catalog.status_code == 200
        assert catalog.json() == {"ids": []}
        assert catalog.headers["cache-control"] == "no-store"
        assert catalog.headers["pragma"] == "no-cache"
        assert client.get("/runbooks/published").status_code == 401

        updated = client.patch(
            f"/admin/users/{junior['username']}",
            headers=admin_headers,
            json={"role_level": "pleno", "domain_function": "redes"},
        )
        assert updated.status_code == 200
        assert updated.json()["role_level"] == "pleno"
        assert updated.json()["domain_function"] == "redes"

        provisioned_again = client.post(
            f"/admin/users/{junior['username']}/provisional-token",
            headers=admin_headers,
        )
        assert provisioned_again.status_code == 200
        assert provisioned_again.headers["cache-control"] == "no-store"
        assert provisioned_again.headers["pragma"] == "no-cache"
        new_provisional = provisioned_again.json()["provisional_token"]
        assert new_provisional != provisional_token
        # Emitir recuperação revoga imediatamente o permanente perdido.
        assert client.get("/me", headers=junior_headers).status_code == 401

        exchanged_again = client.post(
            "/auth/exchange",
            headers={
                "Authorization": f"Bearer {new_provisional}",
                "Idempotency-Key": "exchange-recovery-001",
            },
            json={},
        )
        assert exchanged_again.status_code == 200
        replacement_token = exchanged_again.json()["api_token"]
        assert (
            client.get(
                "/me",
                headers={"Authorization": f"Bearer {replacement_token}"},
            ).json()["username"]
            == junior["username"]
        )

        revoked = client.delete(
            f"/admin/users/{junior['username']}", headers=admin_headers
        )
        assert revoked.status_code == 204
        assert (
            client.get(
                "/me",
                headers={"Authorization": f"Bearer {replacement_token}"},
            ).status_code
            == 401
        )

        second_bootstrap = client.post(
            "/bootstrap/admin",
            headers={"Authorization": f"Bearer {'b' * 32}"},
            json={"username": "outro-admin", "domain_function": "plataforma"},
        )
        assert second_bootstrap.status_code == 409


def test_jump_server_provisiona_pleno_sem_expor_autoridade_admin(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "b" * 32)
    monkeypatch.setenv("AUTH_PEPPER", "p" * 32)
    from app.main import create_app

    database = tmp_path / "jump.db"
    pepper = "p" * 32
    service_token = "luc_jump_credencial-de-teste"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        bootstrap_api_key="b" * 32,
        auth_pepper=pepper,
        allow_insecure_dev=True,
        local_storage_root=tmp_path / "playbooks",
    )
    app = create_app(settings)

    with TestClient(app, base_url="http://testserver") as client:
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                INSERT INTO service_credentials
                    (id, name, scope, token_hash, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    str(uuid4()),
                    "jump-server",
                    "jump_enrollment",
                    digest_api_token(service_token, pepper),
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )

        headers = {
            "Authorization": f"Bearer {service_token}",
            "Idempotency-Key": "jump-enroll-001",  # gitleaks:allow
        }
        missing_domain = client.post(
            "/auth/jump/enroll",
            headers=headers,
            json={"username": "U000001"},
        )
        assert missing_domain.status_code == 422
        assert "domain_function" in missing_domain.json()["detail"]

        created = client.post(
            "/auth/jump/enroll",
            headers=headers,
            json={
                "username": "U000001",
                "domain_function": "servidores",
            },
        )
        assert created.status_code == 200
        assert created.headers["cache-control"] == "no-store"
        assert created.json()["role_level"] == "pleno"
        assert created.json()["domain_function"] == "servidores"
        provisional = created.json()["provisional_token"]

        retry = client.post(
            "/auth/jump/enroll",
            headers=headers,
            json={"username": "U000001"},
        )
        assert retry.status_code == 200
        assert retry.json()["provisional_token"] == provisional

        with sqlite3.connect(database) as connection:
            connection.executemany(
                """
                INSERT INTO users
                    (id, username, api_token_hash, provisional_token_hash,
                     provisional_expires_at, provisional_exchange_key_hash,
                     role_level, domain_function, is_active)
                VALUES (?, ?, NULL, NULL, NULL, NULL, ?, ?, 1)
                """,
                (
                    (str(uuid4()), "U000002", "senior", "redes"),
                    (str(uuid4()), "U000003", "admin", "plataforma"),
                ),
            )

        existing_senior = client.post(
            "/auth/jump/enroll",
            headers={
                "Authorization": f"Bearer {service_token}",
                "Idempotency-Key": "jump-enroll-senior-001",  # gitleaks:allow
            },
            json={"username": "U000002"},
        )
        assert existing_senior.status_code == 200
        assert existing_senior.json()["role_level"] == "senior"
        assert existing_senior.json()["domain_function"] == "redes"

        existing_admin = client.post(
            "/auth/jump/enroll",
            headers={
                "Authorization": f"Bearer {service_token}",
                "Idempotency-Key": "jump-enroll-admin-001",
            },
            json={"username": "U000003"},
        )
        assert existing_admin.status_code == 409

        assert client.post(
            "/auth/jump/enroll",
            headers={
                "Authorization": "Bearer luc_token-de-usuario",
                "Idempotency-Key": "jump-enroll-002",  # gitleaks:allow
            },
            json={"username": "F01234"},
        ).status_code == 401

        assert client.post(
            "/auth/jump/enroll",
            headers={
                "Authorization": f"Bearer {service_token}",
                "Idempotency-Key": "jump-enroll-003",  # gitleaks:allow
            },
            json={"username": "usuario-invalido", "domain_function": "acessos"},
        ).status_code == 422


async def _repositorio_com_dois_admins(tmp_path: Path, nome: str):
    """Repositório novo com dois admins ativos, o mínimo para a corrida."""
    from app.domain.models import RoleLevel
    from app.infrastructure.database import SQLAlchemyJobRepository

    repository = SQLAlchemyJobRepository(
        f"sqlite+aiosqlite:///{(tmp_path / nome).as_posix()}"
    )
    await repository.initialize()
    primeiro = await repository.create_user(
        "admin-a", "a" * 64, RoleLevel.ADMIN, "plataforma"
    )
    segundo = await repository.create_user(
        "admin-b", "b" * 64, RoleLevel.ADMIN, "plataforma"
    )
    return repository, primeiro, segundo


async def test_revogacoes_cruzadas_nao_deixam_o_hub_sem_admin(
    tmp_path: Path,
) -> None:
    """Dois admins se revogando ao mesmo tempo: um passa, o outro é recusado.

    Cada requisição contava dois admins ativos antes de gravar, e as duas
    gravavam. O Hub ficava sem ninguém para criar usuário ou conceder área, e
    voltar exigia o console local da máquina.
    """
    from app.application import IdentityService
    from app.domain.models import SecurityContext
    from app.domain.ports import ConflictError

    repository, primeiro, segundo = await _repositorio_com_dois_admins(
        tmp_path, "revogacao-cruzada.db"
    )
    try:
        service = IdentityService(repository, "pepper-de-teste")
        resultados = await asyncio.gather(
            service.revoke_user(
                SecurityContext.from_user(primeiro), segundo.id
            ),
            service.revoke_user(
                SecurityContext.from_user(segundo), primeiro.id
            ),
            return_exceptions=True,
        )

        recusas = [item for item in resultados if isinstance(item, ConflictError)]
        assert len(recusas) == 1, resultados
        assert await repository.count_active_admins() == 1
    finally:
        await repository.close()


async def test_rebaixamento_simultaneo_a_revogacao_preserva_um_admin(
    tmp_path: Path,
) -> None:
    """O invariante não tem só um caminho: rebaixar também tira um admin."""
    from app.domain.models import RoleLevel
    from app.domain.ports import ConflictError

    repository, primeiro, segundo = await _repositorio_com_dois_admins(
        tmp_path, "rebaixamento-cruzado.db"
    )
    try:
        resultados = await asyncio.gather(
            repository.revoke_user(primeiro.id),
            repository.update_user_scopes(segundo.id, RoleLevel.PLENO, None),
            return_exceptions=True,
        )

        recusas = [item for item in resultados if isinstance(item, ConflictError)]
        assert len(recusas) == 1, resultados
        assert await repository.count_active_admins() == 1
    finally:
        await repository.close()


async def test_ultimo_admin_nao_pode_ser_rebaixado(tmp_path: Path) -> None:
    from app.domain.models import RoleLevel
    from app.domain.ports import ConflictError

    repository, primeiro, segundo = await _repositorio_com_dois_admins(
        tmp_path, "ultimo-admin.db"
    )
    try:
        # Com dois, rebaixar um é legítimo.
        rebaixado = await repository.update_user_scopes(
            segundo.id, RoleLevel.PLENO, None
        )
        assert rebaixado.role_level is RoleLevel.PLENO
        assert await repository.count_active_admins() == 1

        # O que resta, não.
        with pytest.raises(ConflictError):
            await repository.update_user_scopes(primeiro.id, RoleLevel.SENIOR, None)
        with pytest.raises(ConflictError):
            await repository.revoke_user(primeiro.id)
        assert await repository.count_active_admins() == 1

        # Alterações que não mexem no nível seguem permitidas.
        movido = await repository.update_user_scopes(primeiro.id, None, "redes")
        assert movido.domain_function == "redes"
        assert movido.role_level is RoleLevel.ADMIN
    finally:
        await repository.close()
