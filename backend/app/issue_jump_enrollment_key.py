"""Rotaciona offline a credencial M2M exclusiva do jump server."""

import asyncio
import secrets

from app.config import Settings
from app.domain.audit import audit_event
from app.infrastructure.audit import configure_audit_logging
from app.infrastructure.database import SQLAlchemyJobRepository
from app.domain.credentials import digest_api_token


async def _issue() -> None:
    settings = Settings()
    repository = SQLAlchemyJobRepository(settings.database_url)
    token = f"luc_jump_{secrets.token_urlsafe(32)}"
    token_hash = digest_api_token(
        token, settings.auth_pepper.get_secret_value()
    )
    configure_audit_logging()
    await repository.initialize()
    try:
        await repository.rotate_service_credential(
            "jump-server", "jump_enrollment", token_hash
        )
        audit_event(
            "service_credential.rotate",
            actor_id="local-console",
            credential_name="jump-server",
            scope="jump_enrollment",
        )
    finally:
        await repository.close()
    print("Credencial M2M do jump server (exibida uma única vez):")
    print(token)


if __name__ == "__main__":
    asyncio.run(_issue())
