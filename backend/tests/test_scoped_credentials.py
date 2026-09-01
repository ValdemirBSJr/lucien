"""Credencial permanente por escopo -- o problema real que motivou a etapa.

Uma identidade gerida pelo jump server tem o token reemitido a cada login
SSH. Antes desta mudanca, isso sobrescrevia incondicionalmente a unica coluna
de token permanente que existia (`users.api_token_hash`) -- entao qualquer
credencial usada fora do jump (app desktop, por exemplo) morria no proximo
login. Estes testes provam que os dois mundos ficam isolados.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.models import RoleLevel
from app.domain.ports import ConflictError
from app.infrastructure.database import SQLAlchemyJobRepository


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'scoped.db').as_posix()}"
    instance = SQLAlchemyJobRepository(database_url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=4)


async def test_escopo_ausente_preserva_o_comportamento_legado(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_provisioned_user(
        "legado", "prov-hash-1", _expires_at(), RoleLevel.PLENO, "servidores"
    )
    exchanged = await repository.exchange_provisional_token(
        "prov-hash-1", "api-hash-1", "idem-hash-1", datetime.now(timezone.utc)
    )
    assert exchanged.id == user.id
    found = await repository.find_user_by_token_hash("api-hash-1")
    assert found is not None and found.id == user.id


async def test_escopo_nomeado_nunca_toca_a_coluna_legada(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_provisioned_user(
        "escopo-jump",
        "prov-hash-jump",
        _expires_at(),
        RoleLevel.PLENO,
        "servidores",
        scope="jump",
    )
    await repository.exchange_provisional_token(
        "prov-hash-jump", "api-hash-jump", "idem-hash-jump", datetime.now(timezone.utc)
    )
    # A coluna legada nunca foi tocada -- so a credencial de escopo autentica.
    assert await repository.find_user_by_token_hash("api-hash-jump") is not None

    async with repository._sessions() as session:  # type: ignore[attr-defined]
        from app.infrastructure.database import UserRow

        row = await session.get(UserRow, user.id)
        assert row is not None
        assert row.api_token_hash is None


async def test_reemissao_do_escopo_jump_nao_derruba_credencial_pessoal(
    repository: SQLAlchemyJobRepository,
) -> None:
    """O cenario exato do bug relatado: reautenticar no jump nao pode matar
    um token pessoal que a pessoa esta usando em outro lugar."""

    user = await repository.create_provisioned_user(
        "pessoa-do-jump",
        "prov-hash-jump-1",
        _expires_at(),
        RoleLevel.PLENO,
        "servidores",
        scope="jump",
    )
    await repository.exchange_provisional_token(
        "prov-hash-jump-1", "api-hash-jump-1", "idem-1", datetime.now(timezone.utc)
    )

    # A pessoa recebe (uma vez) uma credencial pessoal, direta, sem provisorio.
    assert not await repository.has_user_credential(user.id, "personal")
    await repository.issue_permanent_credential(user.id, "personal", "api-hash-personal")
    assert await repository.has_user_credential(user.id, "personal")

    # Ela loga de novo no jump: reemissao + troca do escopo "jump".
    await repository.issue_provisional_token(
        user.id, "prov-hash-jump-2", _expires_at(), scope="jump"
    )
    await repository.exchange_provisional_token(
        "prov-hash-jump-2", "api-hash-jump-2", "idem-2", datetime.now(timezone.utc)
    )

    # A credencial pessoal continua valendo; a de jump velha morreu; a nova funciona.
    assert await repository.find_user_by_token_hash("api-hash-personal") is not None
    assert await repository.find_user_by_token_hash("api-hash-jump-1") is None
    assert await repository.find_user_by_token_hash("api-hash-jump-2") is not None


async def test_issue_permanent_credential_recusa_escopo_ja_ocupado(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_user(
        "duplo", "api-hash-base", RoleLevel.PLENO, "servidores"
    )
    await repository.issue_permanent_credential(user.id, "personal", "api-hash-p1")
    with pytest.raises(ConflictError):
        await repository.issue_permanent_credential(user.id, "personal", "api-hash-p2")


async def test_revogacao_invalida_credencial_de_qualquer_escopo(
    repository: SQLAlchemyJobRepository,
) -> None:
    await repository.create_user(
        "admin-raiz", "api-hash-admin", RoleLevel.ADMIN, "plataforma"
    )
    user = await repository.create_user(
        "vai-ser-revogado", "api-hash-legado", RoleLevel.PLENO, "servidores"
    )
    await repository.issue_permanent_credential(user.id, "personal", "api-hash-pessoal")
    assert await repository.find_user_by_token_hash("api-hash-pessoal") is not None

    await repository.revoke_user(user.id)

    assert await repository.find_user_by_token_hash("api-hash-legado") is None
    assert await repository.find_user_by_token_hash("api-hash-pessoal") is None
    # sanity: o admin nao foi afetado
    assert await repository.find_user_by_token_hash("api-hash-admin") is not None
