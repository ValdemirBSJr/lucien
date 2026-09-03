"""Readmissão de identidade revogada.

Revogar era um caminho sem volta pelo produto: apaga todos os hashes, e
`issue_provisional_token` recusa quem está inativo. Readmitir alguém --
volta de licença, transferência revertida -- exigia `UPDATE` no banco, fora
de qualquer trilha de auditoria.

O que estes testes fixam é o contorno do comando: ele devolve a identidade e
uma credencial nova, e não ressuscita nenhuma das antigas.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.models import RoleLevel
from app.domain.ports import ConflictError, NotFoundError
from app.infrastructure.database import SQLAlchemyJobRepository


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'reinstate.db').as_posix()}"
    instance = SQLAlchemyJobRepository(database_url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _daqui_a_quatro_horas() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=4)


async def test_readmissao_devolve_acesso_sem_ressuscitar_credencial_antiga(
    repository: SQLAlchemyJobRepository,
) -> None:
    await repository.create_user(
        "admin-raiz", "api-hash-admin", RoleLevel.ADMIN, "plataforma"
    )
    user = await repository.create_user(
        "readmitido", "api-hash-legado", RoleLevel.PLENO, "servidores"
    )
    await repository.issue_permanent_credential(user.id, "personal", "api-hash-pessoal")
    await repository.revoke_user(user.id)

    voltou = await repository.reinstate_user(
        user.id, "hash-provisorio-novo", _daqui_a_quatro_horas()
    )

    assert voltou.is_active is True
    # Papel e área sobrevivem: readmitir não é recriar.
    assert voltou.role_level is RoleLevel.PLENO
    assert voltou.domain_function == "servidores"

    # O ponto do teste: o que foi revogado continua morto. Revogar por
    # vazamento é o caso principal, e ressuscitar o hash antigo devolveria
    # acesso a quem estiver com o token que motivou a revogação.
    assert await repository.find_user_by_token_hash("api-hash-legado") is None
    assert await repository.find_user_by_token_hash("api-hash-pessoal") is None


async def test_provisoria_emitida_na_readmissao_funciona(
    repository: SQLAlchemyJobRepository,
) -> None:
    user = await repository.create_user(
        "volta-do-afastamento", "api-hash-legado", RoleLevel.SENIOR, "redes"
    )
    await repository.revoke_user(user.id)
    await repository.reinstate_user(
        user.id, "hash-provisorio", _daqui_a_quatro_horas()
    )

    # Sem isto, o comando devolveria alguém ativo e sem forma de entrar.
    trocado = await repository.exchange_provisional_token(
        "hash-provisorio",
        "api-hash-recem-emitido",
        "chave-de-troca",
        datetime.now(timezone.utc),
    )
    assert trocado.id == user.id
    assert await repository.find_user_by_token_hash("api-hash-recem-emitido") is not None


async def test_readmitir_quem_esta_ativo_e_recusado(
    repository: SQLAlchemyJobRepository,
) -> None:
    """Parece inofensivo e derrubaria a sessão de quem está trabalhando.

    Reativar quem já está ativo é um no-op, mas a emissão que vem junto
    rotacionaria a credencial -- sem que ninguém tivesse pedido isso.
    """

    user = await repository.create_user(
        "trabalhando", "api-hash-em-uso", RoleLevel.PLENO, "servidores"
    )

    with pytest.raises(ConflictError, match="ativo"):
        await repository.reinstate_user(
            user.id, "hash-provisorio", _daqui_a_quatro_horas()
        )

    # E a credencial de quem estava trabalhando continua valendo.
    assert await repository.find_user_by_token_hash("api-hash-em-uso") is not None


async def test_readmitir_usuario_inexistente_levanta(
    repository: SQLAlchemyJobRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await repository.reinstate_user(
            "11111111-1111-4111-8111-111111111111",
            "hash-provisorio",
            _daqui_a_quatro_horas(),
        )
