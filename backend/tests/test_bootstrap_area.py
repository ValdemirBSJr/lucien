"""A area do primeiro administrador precisa existir.

O `create_user` sempre verificou a area contra RUNBOOK_DOMAIN_FUNCTIONS; o
`bootstrap_admin` nao verificava. O administrador nascia em "plataforma" --
palavra fixa no codigo e no CLI, ausente da lista padrao -- e a publicacao
dele cairia num diretorio que ninguem declarou, sem erro nenhum.
"""

from pathlib import Path

import pytest

from app.application import IdentityService, ValidationError
from app.domain.models import ADMIN_DOMAIN_FUNCTION, DEFAULT_DOMAIN_FUNCTIONS
from app.infrastructure.database import SQLAlchemyJobRepository


async def _servico(
    tmp_path: Path,
    nome: str,
    dominios: tuple[str, ...] = DEFAULT_DOMAIN_FUNCTIONS,
) -> IdentityService:
    repository = SQLAlchemyJobRepository(
        f"sqlite+aiosqlite:///{(tmp_path / nome).as_posix()}"
    )
    await repository.initialize()
    return IdentityService(repository, "pepper-de-teste", domain_functions=dominios)


def test_area_do_admin_e_declarada_por_padrao() -> None:
    """A area do administrador nao pode ser invisivel para o proprio Hub."""

    assert ADMIN_DOMAIN_FUNCTION in DEFAULT_DOMAIN_FUNCTIONS


@pytest.mark.asyncio
async def test_bootstrap_sem_area_usa_a_area_do_administrador(tmp_path: Path) -> None:
    servico = await _servico(tmp_path, "sem-area.db")

    admin, _ = await servico.bootstrap_admin("administrator")

    assert admin.domain_function == ADMIN_DOMAIN_FUNCTION


@pytest.mark.asyncio
async def test_bootstrap_recusa_area_nao_declarada(tmp_path: Path) -> None:
    """Era exatamente este o caminho que escapava.

    "plataforma" foi o valor que o CLI mandava fixo e que o schema trazia como
    padrao; nenhuma instalacao o declarava.
    """

    servico = await _servico(tmp_path, "area-invalida.db")

    with pytest.raises(ValidationError) as excecao:
        await servico.bootstrap_admin("administrator", "plataforma")

    mensagem = str(excecao.value)
    assert "plataforma" in mensagem
    # A recusa precisa dizer o que existe, senao o operador fica adivinhando.
    assert ADMIN_DOMAIN_FUNCTION in mensagem


@pytest.mark.asyncio
async def test_bootstrap_aceita_area_declarada_explicitamente(tmp_path: Path) -> None:
    servico = await _servico(tmp_path, "area-explicita.db")

    admin, _ = await servico.bootstrap_admin("administrator", "networks")

    assert admin.domain_function == "networks"


@pytest.mark.asyncio
async def test_lista_propria_sem_a_area_do_admin_recusa_e_explica(
    tmp_path: Path,
) -> None:
    """Recusar e melhor que cair na primeira area da lista.

    Numa instalacao com areas proprias, o administrador apareceria dentro de
    uma area operacional sem ninguem ter pedido -- e isso so seria notado
    quando ele publicasse.
    """

    servico = await _servico(tmp_path, "lista-propria.db", ("networks", "access"))

    with pytest.raises(ValidationError) as excecao:
        await servico.bootstrap_admin("administrator")

    mensagem = str(excecao.value)
    assert "RUNBOOK_DOMAIN_FUNCTIONS" in mensagem
    assert "networks, access" in mensagem


@pytest.mark.asyncio
async def test_bootstrap_recusa_username_invalido(tmp_path: Path) -> None:
    """O bootstrap tambem nao validava a identidade; create_user validava."""

    servico = await _servico(tmp_path, "username-invalido.db")

    with pytest.raises(ValidationError):
        await servico.bootstrap_admin("ab")
