from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from app.git_repository import GitRepository
from app.settings import Settings


class CapturingRunner:
    def __init__(self) -> None:
        self.arguments: Sequence[str] = ()
        self.environment: Mapping[str, str] = {}

    def run(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout_seconds: int,
    ) -> str:
        self.arguments = arguments
        self.environment = environment
        return ""


def test_token_chega_somente_ao_askpass_e_nao_aos_argumentos(tmp_path: Path) -> None:
    token = "token-read-only-muito-secreto"
    settings = Settings.from_environment(
        {
            "WIKI_REPOSITORY_URL": "https://gitea.example.test/infra/runbooks.git",
            "WIKI_REPOSITORY_USER": "wiki-reader",
            "WIKI_REPOSITORY_TOKEN": token,
            "WIKI_WORKSPACE": str(tmp_path / "workspace"),
            "WIKI_PUBLISH_ROOT": str(tmp_path / "publish"),
        }
    )
    runner = CapturingRunner()
    repository = GitRepository(settings, runner=runner, askpass_path=Path("/safe/askpass"))

    repository._git(("ls-remote", settings.repository_url))

    assert token not in " ".join(runner.arguments)
    assert runner.environment["WIKI_REPOSITORY_TOKEN"] == token
    assert runner.environment["GIT_ASKPASS"] == "/safe/askpass"
    assert runner.environment["GIT_TERMINAL_PROMPT"] == "0"
