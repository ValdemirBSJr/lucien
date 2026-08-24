from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import Settings, SettingsError


def valid_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "WIKI_REPOSITORY_URL": "https://gitea.example.test/infra/runbooks.git",
        "WIKI_REPOSITORY_BRANCH": "main",
        "WIKI_REPOSITORY_USER": "wiki-reader",
        "WIKI_REPOSITORY_TOKEN": "token-somente-leitura",
        "WIKI_WORKSPACE": str(tmp_path / "workspace"),
        "WIKI_PUBLISH_ROOT": str(tmp_path / "publish"),
    }


def test_settings_aceita_configuracao_minima_e_oculta_token(tmp_path: Path) -> None:
    settings = Settings.from_environment(valid_environment(tmp_path))

    assert settings.repository_branch == "main"
    assert "token-somente-leitura" not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "http://gitea.example.test/infra/runbooks.git",
        "https://user:secret@gitea.example.test/infra/runbooks.git",
        "https://gitea.example.test/infra/runbooks.git?token=secret",
        "https://gitea.example.test/infra/runbooks",
    ],
)
def test_settings_rejeita_url_insegura(tmp_path: Path, url: str) -> None:
    environment = valid_environment(tmp_path)
    environment["WIKI_REPOSITORY_URL"] = url

    with pytest.raises(SettingsError):
        Settings.from_environment(environment)


@pytest.mark.parametrize("branch", ["-main", "main..evil", "main//evil", "main@{1}"])
def test_settings_rejeita_branch_insegura(tmp_path: Path, branch: str) -> None:
    environment = valid_environment(tmp_path)
    environment["WIKI_REPOSITORY_BRANCH"] = branch

    with pytest.raises(SettingsError):
        Settings.from_environment(environment)


def test_settings_exige_uma_unica_fonte_de_token(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("outro-token-seguro", encoding="utf-8")
    environment = valid_environment(tmp_path)
    environment["WIKI_REPOSITORY_TOKEN_FILE"] = str(token_file)

    with pytest.raises(SettingsError):
        Settings.from_environment(environment)

