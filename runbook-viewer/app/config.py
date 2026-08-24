from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração validada exclusivamente a partir do ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    viewer_hub_url: str = "https://hub:8443"
    viewer_hub_ca_file: Path = Path("/certs/ca.crt")
    viewer_session_secret: SecretStr = SecretStr("")
    viewer_session_secret_file: Path | None = None
    viewer_runbooks_root: Path = Path("/runbooks")
    viewer_session_ttl_seconds: int = 900
    viewer_max_documents: int = 10_000
    viewer_max_file_bytes: int = 1024 * 1024
    # Espelha RBAC_ENTRY_ROLES_ENABLED do Hub apenas para exibir o botão de edição. O
    # Hub reavalia a autorização em cada revisão; divergir aqui não concede nada.
    rbac_entry_roles_enabled: bool = False

    @field_validator("viewer_hub_url")
    @classmethod
    def validate_hub_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("VIEWER_HUB_URL contém porta inválida") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("VIEWER_HUB_URL deve ser uma URL HTTPS sem caminho")
        return normalized

    @field_validator("viewer_session_secret")
    @classmethod
    def validate_session_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw.encode("utf-8")) < 32 or raw.startswith("CHANGE_ME"):
            raise ValueError(
                "VIEWER_SESSION_SECRET deve ter ao menos 32 bytes aleatórios"
            )
        return value

    @field_validator("viewer_session_ttl_seconds")
    @classmethod
    def validate_session_ttl(cls, value: int) -> int:
        if not 60 <= value <= 3600:
            raise ValueError(
                "VIEWER_SESSION_TTL_SECONDS deve ficar entre 60 e 3600"
            )
        return value

    @field_validator("viewer_max_documents")
    @classmethod
    def validate_document_limit(cls, value: int) -> int:
        if not 1 <= value <= 10_000:
            raise ValueError("VIEWER_MAX_DOCUMENTS deve ficar entre 1 e 10000")
        return value

    @field_validator("viewer_max_file_bytes")
    @classmethod
    def validate_file_limit(cls, value: int) -> int:
        if not 1024 <= value <= 10 * 1024 * 1024:
            raise ValueError(
                "VIEWER_MAX_FILE_BYTES deve ficar entre 1 KiB e 10 MiB"
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def load_session_secret_file(cls, raw_values: Any) -> Any:
        """Lê o segredo de sessão montado pelo Compose sem expô-lo no inspect."""

        if not isinstance(raw_values, dict):
            return raw_values
        values = dict(raw_values)
        direct_value = values.get("viewer_session_secret", "")
        direct = (
            direct_value.get_secret_value()
            if isinstance(direct_value, SecretStr)
            else str(direct_value)
        )
        path_value = values.get("viewer_session_secret_file")
        path = Path(path_value) if path_value is not None else None
        if direct and path is not None:
            raise ValueError(
                "informe somente VIEWER_SESSION_SECRET ou VIEWER_SESSION_SECRET_FILE"
            )
        if path is not None:
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise ValueError(
                    "VIEWER_SESSION_SECRET_FILE deve ser um arquivo regular absoluto"
                )
            if path.stat().st_size > 65_536:
                raise ValueError("VIEWER_SESSION_SECRET_FILE excede 64 KiB")
            direct = path.read_text(encoding="utf-8").rstrip("\r\n")
        if not direct:
            raise ValueError("VIEWER_SESSION_SECRET é obrigatório")
        if any(character in direct for character in "\r\n\x00"):
            raise ValueError("VIEWER_SESSION_SECRET contém caracteres inválidos")
        if len(direct.encode("utf-8")) < 32 or direct.startswith("CHANGE_ME"):
            raise ValueError(
                "VIEWER_SESSION_SECRET deve ter ao menos 32 bytes aleatórios"
            )
        values["viewer_session_secret"] = direct
        return values
