from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def _secret_file(root: Path, name: str, value: str) -> Path:
    path = root / name
    path.write_text(value, encoding="utf-8")
    return path


def test_settings_le_segredos_de_arquivos(tmp_path: Path) -> None:
    settings = Settings(
        database_url_file=_secret_file(
            tmp_path, "database_url", "sqlite+aiosqlite:///:memory:"
        ),
        bootstrap_api_key_file=_secret_file(tmp_path, "bootstrap", "b" * 32),
        auth_pepper_file=_secret_file(tmp_path, "pepper", "p" * 32),
        git_token_file=_secret_file(tmp_path, "git_token", "token-git"),
    )

    assert settings.database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.bootstrap_api_key.get_secret_value() == "b" * 32
    assert settings.auth_pepper.get_secret_value() == "p" * 32
    assert settings.git_token.get_secret_value() == "token-git"


def test_settings_rejeita_segredo_direto_e_arquivo_simultaneos(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="somente DATABASE_URL"):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            database_url_file=_secret_file(tmp_path, "database_url", "outro"),
            bootstrap_api_key="b" * 32,
            auth_pepper="p" * 32,
        )


def test_settings_rejeita_idioma_de_runbook_desconhecido() -> None:
    with pytest.raises(ValidationError, match="slm_language_runbook"):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            auth_pepper="p" * 32,
            slm_language_runbook="es",  # type: ignore[arg-type]
        )
