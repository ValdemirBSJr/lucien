import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import update

from app.application import JobService
from app.domain.models import RoleLevel, SecurityContext
from app.domain.ports import SecretScanner, SecretScanResult
from app.infrastructure.database import JobRow, SQLAlchemyJobRepository
from app.infrastructure.storage import LocalProvider, MirroredStorage


class _Scanner(SecretScanner):
    async def detect(self, content: str) -> SecretScanResult:
        return SecretScanResult(detected=False)


@pytest.fixture
async def repository(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'catalogo.db').as_posix()}"
    instance = SQLAlchemyJobRepository(database_url)
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _context(user) -> SecurityContext:
    return SecurityContext.from_user(user)


async def _user(repository: SQLAlchemyJobRepository, username: str, role: RoleLevel, domain: str):
    token_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return await repository.create_user(username, token_hash, role, domain)


def _service(repository: SQLAlchemyJobRepository, storage) -> JobService:
    return JobService(repository, _Scanner(), storage, revisions_enabled=True)


async def _publish(repository, service, author, name: str, created_at: datetime | None = None):
    job = await repository.create_job(author.id, name, ("echo ok",), ())
    if created_at is not None:
        async with repository._sessions() as session:
            await session.execute(
                update(JobRow).where(JobRow.id == job.id).values(created_at=created_at)
            )
            await session.commit()
    published, _ = await service.publish(
        _context(author), job.id, "### Step 1: Run\n```bash\necho ok\n```\n", f"publish-{name}"
    )
    return published


async def test_admin_ve_todos_os_dominios(tmp_path: Path, repository: SQLAlchemyJobRepository) -> None:
    service = _service(repository, LocalProvider(tmp_path / "playbooks"))
    autor_servidores = await _user(repository, "autor-servidores", RoleLevel.SENIOR, "servidores")
    autor_redes = await _user(repository, "autor-redes", RoleLevel.SENIOR, "redes")
    admin = await _user(repository, "admin-global", RoleLevel.ADMIN, "platform")

    publicado_servidores = await _publish(repository, service, autor_servidores, "job-servidores")
    publicado_redes = await _publish(repository, service, autor_redes, "job-redes")

    entradas = {
        entrada.id: entrada
        for entrada in await service.list_published_runbooks_for(_context(admin))
    }
    assert publicado_servidores.id in entradas
    assert publicado_redes.id in entradas
    assert entradas[publicado_servidores.id].name == "job-servidores"


async def test_usuario_comum_so_ve_seu_dominio(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    service = _service(repository, LocalProvider(tmp_path / "playbooks"))
    autor_servidores = await _user(repository, "autor-servidores-2", RoleLevel.SENIOR, "servidores")
    autor_redes = await _user(repository, "autor-redes-2", RoleLevel.SENIOR, "redes")
    leitor_servidores = await _user(
        repository, "leitor-servidores", RoleLevel.SENIOR, "servidores"
    )

    publicado_servidores = await _publish(repository, service, autor_servidores, "job-servidores-2")
    publicado_redes = await _publish(repository, service, autor_redes, "job-redes-2")

    ids = {
        entrada.id
        for entrada in await service.list_published_runbooks_for(_context(leitor_servidores))
    }
    assert publicado_servidores.id in ids
    assert publicado_redes.id not in ids


async def test_usuario_com_area_extra_ve_as_duas(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    service = _service(repository, LocalProvider(tmp_path / "playbooks"))
    autor_servidores = await _user(repository, "autor-servidores-3", RoleLevel.SENIOR, "servidores")
    autor_redes = await _user(repository, "autor-redes-3", RoleLevel.SENIOR, "redes")
    leitor_multi = await _user(repository, "leitor-multi", RoleLevel.SENIOR, "servidores")
    leitor_multi = await repository.update_user_scopes(
        leitor_multi.id, None, None, extra_domains=("redes",)
    )

    publicado_servidores = await _publish(repository, service, autor_servidores, "job-servidores-3")
    publicado_redes = await _publish(repository, service, autor_redes, "job-redes-3")

    entradas = {
        entrada.id: entrada
        for entrada in await service.list_published_runbooks_for(_context(leitor_multi))
    }
    assert publicado_servidores.id in entradas
    assert publicado_redes.id in entradas
    # A area de cada item e a do runbook, nao a do leitor: o que vem
    # congelado na publicacao.
    assert entradas[publicado_servidores.id].domain_function == "servidores"
    assert entradas[publicado_redes.id].domain_function == "redes"


async def test_revisao_marca_a_versao_anterior_como_superada(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """A versao superada continua listada, mas com `latest=False`.

    O `revise` a recusaria com conflito, entao o CLI a esconde; o app desktop
    a abre em modo leitura, entao a rota nao a omite.
    """

    service = _service(repository, LocalProvider(tmp_path / "playbooks"))
    autor = await _user(repository, "autor-revisao", RoleLevel.SENIOR, "servidores")
    original = await _publish(repository, service, autor, "job-com-revisao")

    revisao, _ = await service.revise(
        _context(autor),
        original.id,
        original.content_hash,
        "### Step 1: Run\n```bash\necho revisado\n```\n",
        "revise-job-com-revisao",
    )

    entradas = {
        entrada.id: entrada
        for entrada in await service.list_published_runbooks_for(_context(autor))
    }
    assert entradas[original.id].latest is False
    assert entradas[revisao.id].latest is True
    assert entradas[revisao.id].domain_function == "servidores"


async def test_data_de_publicacao_vem_do_espelho_e_nao_do_upload(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """`created_at` do job e a hora do upload, dias antes do envio as vezes."""

    service = _service(
        repository, MirroredStorage(LocalProvider(tmp_path / "playbooks"), repository)
    )
    autor = await _user(repository, "autor-data", RoleLevel.SENIOR, "servidores")
    upload_antigo = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)

    antes = datetime.now(timezone.utc)
    publicado = await _publish(repository, service, autor, "job-com-data", upload_antigo)

    entrada = next(
        entrada
        for entrada in await service.list_published_runbooks_for(_context(autor))
        if entrada.id == publicado.id
    )
    assert entrada.published_at.tzinfo is not None
    assert entrada.published_at >= antes - timedelta(seconds=5)


async def test_sem_linha_no_espelho_a_data_recua_para_o_upload(
    tmp_path: Path, repository: SQLAlchemyJobRepository
) -> None:
    """Instalacao anterior ao espelho, sem backfill: melhor o upload que nada."""

    service = _service(repository, LocalProvider(tmp_path / "playbooks"))
    autor = await _user(repository, "autor-sem-espelho", RoleLevel.SENIOR, "servidores")
    upload_antigo = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)

    publicado = await _publish(repository, service, autor, "job-sem-espelho", upload_antigo)

    entrada = next(
        entrada
        for entrada in await service.list_published_runbooks_for(_context(autor))
        if entrada.id == publicado.id
    )
    assert entrada.published_at == upload_antigo
