"""Recuperação offline da credencial do administrador.

Este módulo não registra rota HTTP. A autoridade para executá-lo é o acesso
administrativo ao contêiner do Hub e aos secrets montados nele.
"""

import argparse
import asyncio
import sys
from datetime import datetime

from app.application import IdentityService
from app.config import Settings
from app.domain.ports import DomainError
from app.infrastructure.audit import configure_audit_logging
from app.infrastructure.database import SQLAlchemyJobRepository


async def _recover(identifier: str) -> tuple[str, str, datetime]:
    settings = Settings()
    repository = SQLAlchemyJobRepository(settings.database_url)
    configure_audit_logging()
    await repository.initialize()
    try:
        service = IdentityService(
            repository, settings.auth_pepper.get_secret_value()
        )
        user, token, expires_at = await service.recover_admin_token(identifier)
        return user.username, token, expires_at
    finally:
        await repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotaciona offline o token de um administrador ativo."
    )
    parser.add_argument("identifier", help="UUID ou username do administrador")
    args = parser.parse_args()

    try:
        username, token, expires_at = asyncio.run(_recover(args.identifier))
    except DomainError as error:
        print(f"Recuperação recusada: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(
        f"Token provisório de {username}; expira em {expires_at.isoformat()} "
        "e será exibido somente nesta execução:"
    )
    print(token)
    print("No cliente desse administrador, execute 'lucien login' dentro de 4 horas.")


if __name__ == "__main__":
    main()
