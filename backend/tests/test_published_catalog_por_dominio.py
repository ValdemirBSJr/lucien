import hashlib
from pathlib import Path

import pytest

from app.application import JobService
from app.domain.models import RoleLevel, SecurityContext
from app.domain.ports import SecretScanner, SecretScanResult
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.storage import LocalProvider


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


def _service(repository: SQLAlchemyJobRepository, storage: LocalProvider) -> JobService:
    return JobService(repository, _Scanner(), storage, revisions_enabled=True)


async def _publish(repository, service, author, name: str):
    job = await repository.create_job(author.id, name, ("echo ok",), ())
    published, _ = await service.publish(
        _context(author), job.id, "### Step 1: Run\n```bash\necho ok\n```\n", f"publish-{name}"
    )
    return published


async def test_admin_ve_todos_os_dominios(tmp_path: Path, repository: SQLAlchemyJobRepository) -> None:
    service = _service(repository, LocalProvider(tmp_path / "playbooks"))
    autor_servidores = await _user(repository, "autor-servidores", RoleLevel.SENIOR, "servidores")
    autor_redes = await _user(repository, "autor-redes", RoleLevel.SENIOR, "redes")
    admin = await _user(repository, "admin-global", RoleLevel.ADMIN, "plataforma")

    publicado_servidores = await _publish(repository, service, autor_servidores, "job-servidores")
    publicado_redes = await _publish(repository, service, autor_redes, "job-redes")

    pares = await service.list_published_runbooks_for(_context(admin))
    ids = {id_ for id_, _ in pares}
    assert publicado_servidores.id in ids
    assert publicado_redes.id in ids
    assert (publicado_servidores.id, "job-servidores") in pares


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

    pares = await service.list_published_runbooks_for(_context(leitor_servidores))
    ids = {id_ for id_, _ in pares}
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

    pares = await service.list_published_runbooks_for(_context(leitor_multi))
    ids = {id_ for id_, _ in pares}
    assert publicado_servidores.id in ids
    assert publicado_redes.id in ids
