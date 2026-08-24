import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.models import DEFAULT_DOMAIN_FUNCTIONS


class Settings(BaseSettings):
    """Configuração validada exclusivamente a partir do ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = ""
    database_url_file: Path | None = None
    bootstrap_api_key: SecretStr = SecretStr("")
    bootstrap_api_key_file: Path | None = None
    auth_pepper: SecretStr = SecretStr("")
    auth_pepper_file: Path | None = None
    user_creation_enabled: bool = False
    rbac_entry_roles_enabled: bool = False
    # Funcoes de dominio aceitas em `lucien start -r`; cada uma vira um
    # diretorio no destino publicado. O padrao preserva a lista que antes
    # estava fixa no schema de enrollment.
    runbook_domain_functions: str = ",".join(DEFAULT_DOMAIN_FUNCTIONS)
    allow_insecure_dev: bool = False

    slm_base_url: str = "http://slm:11434"
    slm_model: str = "qwen2.5-coder:3b"
    slm_language_runbook: Literal["pt-br", "en"] = "pt-br"
    slm_timeout_seconds: float = 300.0
    slm_num_thread: int = 0
    # Janela de contexto pedida ao runtime. Zero preserva o padrao dele, que
    # e 2048 e corta o prompt em silencio quando ele nao cabe.
    slm_num_ctx: int = 8192
    # Teto do log reduzido enviado a SLM. Menor barateia cada tentativa e
    # ajuda o modelo a nao perder a instrucao no volume; maior da mais
    # contexto quando a sessao e curta e o host aguenta.
    slm_prompt_max_chars: int = 8_000
    slm_enrichment_enabled: bool = True
    runbook_enricher: Literal["slm", "deterministic"] = "slm"
    upload_worker_poll_seconds: float = 2.0
    upload_worker_lease_seconds: int = 900
    upload_worker_retry_base_seconds: int = 10
    upload_worker_max_attempts: int = 5
    max_log_bytes: int = 2 * 1024 * 1024
    secret_scanner_url: str = "http://secret-scanner:8090"
    secret_scanner_timeout_seconds: float = 5.0

    storage_provider: Literal["local", "github", "gitea"] = "local"
    local_storage_root: Path = Path("/data/playbooks")
    git_api_base: str = "https://api.github.com"
    git_owner: str = ""
    git_repo: str = ""
    git_branch: str = "main"
    git_docs_prefix: str = "docs/runbooks"
    git_token: SecretStr = SecretStr("")
    git_token_file: Path | None = None
    # CA adicional para instâncias Git corporativas. O trust store do sistema
    # continua ativo; este arquivo apenas acrescenta uma âncora de confiança.
    git_ca_file: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def load_secret_files(cls, raw_values: Any) -> Any:
        """Resolve secrets de arquivo sem expô-los na configuração do contêiner."""

        if not isinstance(raw_values, dict):
            return raw_values
        values = dict(raw_values)
        values["database_url"] = cls._resolve_file_secret(
            "DATABASE_URL",
            values.get("database_url"),
            values.get("database_url_file"),
            required=True,
        )
        values["bootstrap_api_key"] = cls._resolve_file_secret(
            "BOOTSTRAP_API_KEY",
            values.get("bootstrap_api_key"),
            values.get("bootstrap_api_key_file"),
            required=False,
        )
        values["auth_pepper"] = cls._resolve_file_secret(
            "AUTH_PEPPER",
            values.get("auth_pepper"),
            values.get("auth_pepper_file"),
            required=True,
        )
        values["git_token"] = cls._resolve_file_secret(
            "GIT_TOKEN",
            values.get("git_token"),
            values.get("git_token_file"),
            required=False,
        )
        return values

    @staticmethod
    def _resolve_file_secret(
        name: str,
        direct_value: str | SecretStr | None,
        file_path: Path | str | None,
        *,
        required: bool,
    ) -> str:
        if isinstance(direct_value, SecretStr):
            direct_value = direct_value.get_secret_value()
        if direct_value and file_path is not None:
            raise ValueError(f"informe somente {name} ou {name}_FILE")
        if file_path is not None:
            file_path = Path(file_path)
            if (
                not file_path.is_absolute()
                or file_path.is_symlink()
                or not file_path.is_file()
            ):
                raise ValueError(f"{name}_FILE deve ser um arquivo regular absoluto")
            if file_path.stat().st_size > 65_536:
                raise ValueError(f"{name}_FILE excede 64 KiB")
            direct_value = file_path.read_text(encoding="utf-8").rstrip("\r\n")
        value = direct_value or ""
        if required and not value:
            raise ValueError(f"{name} é obrigatório")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError(f"{name} contém caracteres inválidos")
        return value

    @field_validator("auth_pepper")
    @classmethod
    def validate_long_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32 or raw.startswith("CHANGE_ME"):
            raise ValueError("o segredo deve ter ao menos 32 bytes aleatórios")
        return value

    @field_validator("bootstrap_api_key")
    @classmethod
    def validate_optional_bootstrap_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw and (len(raw) < 32 or raw.startswith("CHANGE_ME")):
            raise ValueError("BOOTSTRAP_API_KEY deve ter ao menos 32 bytes aleatórios")
        return value

    @model_validator(mode="after")
    def require_bootstrap_when_enabled(self) -> "Settings":
        if (
            self.user_creation_enabled
            and not self.bootstrap_api_key.get_secret_value()
        ):
            raise ValueError(
                "BOOTSTRAP_API_KEY é obrigatória quando USER_CREATION_ENABLED=true"
            )
        return self

    @model_validator(mode="after")
    def validate_worker_lease_covers_slm_calls(self) -> "Settings":
        minimum = int(self.slm_timeout_seconds * 2) + 30
        if self.upload_worker_lease_seconds < minimum:
            raise ValueError(
                "UPLOAD_WORKER_LEASE_SECONDS deve cobrir duas chamadas à SLM "
                "mais 30 segundos"
            )
        return self

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL é obrigatória")
        return value

    @field_validator("max_log_bytes")
    @classmethod
    def validate_log_limit(cls, value: int) -> int:
        if value < 1024 or value > 10 * 1024 * 1024:
            raise ValueError("MAX_LOG_BYTES deve ficar entre 1 KiB e 10 MiB")
        return value

    @field_validator("slm_timeout_seconds")
    @classmethod
    def validate_slm_timeout(cls, value: float) -> float:
        if not 1 <= value <= 900:
            raise ValueError("SLM_TIMEOUT_SECONDS deve ficar entre 1 e 900")
        return value

    @field_validator("slm_num_thread")
    @classmethod
    def validate_slm_num_thread(cls, value: int) -> int:
        if not 0 <= value <= 64:
            raise ValueError("SLM_NUM_THREAD deve ficar entre 0 e 64")
        return value

    @field_validator("slm_num_ctx")
    @classmethod
    def validate_slm_num_ctx(cls, value: int) -> int:
        if value < 0 or value > 131_072:
            raise ValueError("SLM_NUM_CTX deve ficar entre 0 e 131072")
        return value

    @field_validator("slm_prompt_max_chars")
    @classmethod
    def validate_slm_prompt_max_chars(cls, value: int) -> int:
        if not 500 <= value <= 200_000:
            raise ValueError(
                "SLM_PROMPT_MAX_CHARS deve ficar entre 500 e 200000"
            )
        return value

    @field_validator("upload_worker_poll_seconds")
    @classmethod
    def validate_worker_poll(cls, value: float) -> float:
        if not 0.1 <= value <= 60:
            raise ValueError("UPLOAD_WORKER_POLL_SECONDS deve ficar entre 0,1 e 60")
        return value

    @field_validator("upload_worker_lease_seconds")
    @classmethod
    def validate_worker_lease(cls, value: int) -> int:
        if not 60 <= value <= 3600:
            raise ValueError("UPLOAD_WORKER_LEASE_SECONDS deve ficar entre 60 e 3600")
        return value

    @field_validator("upload_worker_retry_base_seconds")
    @classmethod
    def validate_worker_retry(cls, value: int) -> int:
        if not 1 <= value <= 300:
            raise ValueError(
                "UPLOAD_WORKER_RETRY_BASE_SECONDS deve ficar entre 1 e 300"
            )
        return value

    @field_validator("upload_worker_max_attempts")
    @classmethod
    def validate_worker_attempts(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("UPLOAD_WORKER_MAX_ATTEMPTS deve ficar entre 1 e 20")
        return value

    @field_validator("secret_scanner_url")
    @classmethod
    def validate_secret_scanner_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("SECRET_SCANNER_URL contém porta inválida") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("SECRET_SCANNER_URL deve ser uma URL HTTP(S) sem caminho")
        return normalized

    @field_validator("secret_scanner_timeout_seconds")
    @classmethod
    def validate_secret_scanner_timeout(cls, value: float) -> float:
        if not 0.1 <= value <= 30:
            raise ValueError("SECRET_SCANNER_TIMEOUT_SECONDS deve ficar entre 0,1 e 30")
        return value

    @field_validator("git_api_base")
    @classmethod
    def validate_git_api_base(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("GIT_API_BASE contém porta inválida") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.params
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("GIT_API_BASE deve ser uma URL HTTPS sem credenciais")
        return normalized

    @field_validator("runbook_domain_functions")
    @classmethod
    def validate_runbook_domain_functions(cls, value: str) -> str:
        # O valor vira nome de diretorio no repositorio publicado, entao a
        # gramatica e a mesma de `domain_function` do usuario. Recusar aqui e
        # melhor do que descobrir na hora de gravar o artefato.
        entries = [entry.strip() for entry in value.split(",")]
        normalized: list[str] = []
        for entry in entries:
            if not entry:
                continue
            if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", entry) is None:
                raise ValueError(
                    "RUNBOOK_DOMAIN_FUNCTIONS aceita nomes minusculos de 3 a 64 "
                    f"caracteres, iniciados por letra: {entry!r} e invalido"
                )
            if entry not in normalized:
                normalized.append(entry)
        if not normalized:
            raise ValueError("RUNBOOK_DOMAIN_FUNCTIONS nao pode ficar vazio")
        return ",".join(normalized)

    @property
    def domain_functions(self) -> tuple[str, ...]:
        return tuple(self.runbook_domain_functions.split(","))

    @field_validator("git_docs_prefix")
    @classmethod
    def validate_git_docs_prefix(cls, value: str) -> str:
        normalized = value.strip()
        path = PurePosixPath(normalized)
        if (
            not normalized
            or "\\" in normalized
            or path.is_absolute()
            or re.fullmatch(r"[A-Za-z0-9._/-]+", normalized) is None
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("GIT_DOCS_PREFIX deve ser um caminho POSIX relativo seguro")
        return path.as_posix()
