"""Sincronização Git fechada para um único repositório HTTPS configurado."""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from .settings import Settings
from .tree_guard import validate_repository_tree


class RepositoryError(RuntimeError):
    """Falha segura na sincronização do repositório."""


class CommandRunner:
    """Executa processos sem shell e sem incorporar credenciais aos argumentos."""

    def run(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout_seconds: int,
    ) -> str:
        try:
            result = subprocess.run(
                list(arguments),
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepositoryError("o processo Git não concluiu no tempo esperado") from exc
        if result.returncode != 0:
            # A saída é deliberadamente omitida: ela é externa e pode conter dados sensíveis.
            raise RepositoryError(f"o processo Git encerrou com status {result.returncode}")
        return result.stdout.strip()


def prepare_ca_bundle(settings: Settings, destination: Path = Path("/tmp/lucien-ca-bundle.pem")) -> Path | None:
    """Combina a CA privada com as raízes públicas sem desabilitar a verificação."""

    if settings.gitea_ca_file is None:
        return None
    system_bundle = Path("/etc/ssl/certs/ca-certificates.crt")
    if system_bundle.is_symlink() or not system_bundle.is_file():
        raise RepositoryError("o bundle público de CAs não está disponível")

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with candidate.open("wb") as target:
            for source in (system_bundle, settings.gitea_ca_file):
                payload = source.read_bytes()
                target.write(payload)
                if payload and not payload.endswith(b"\n"):
                    target.write(b"\n")
            target.flush()
            os.fsync(target.fileno())
        candidate.chmod(0o600)
        ssl.create_default_context(cafile=str(candidate))
        os.replace(candidate, destination)
    except (OSError, ssl.SSLError) as exc:
        candidate.unlink(missing_ok=True)
        raise RepositoryError("não foi possível preparar o bundle de CAs") from exc
    return destination


class GitRepository:
    """Adapter Git que ignora hooks, submódulos e configurações do conteúdo."""

    _GIT_GUARDS = (
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "http.followRedirects=false",
        "-c",
        "credential.helper=",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
    )

    def __init__(
        self,
        settings: Settings,
        *,
        runner: CommandRunner | None = None,
        askpass_path: Path = Path("/app/bin/git-askpass.sh"),
    ) -> None:
        self._settings = settings
        self._runner = runner or CommandRunner()
        self._askpass_path = askpass_path
        self.path = settings.workspace / "repository"
        self._ca_bundle = prepare_ca_bundle(settings)

    def _environment(self) -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": "/tmp/lucien-git-home",
            "GIT_ASKPASS": str(self._askpass_path),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "WIKI_REPOSITORY_USER": self._settings.repository_user,
        }
        if self._settings.repository_token is not None:
            environment["WIKI_REPOSITORY_TOKEN"] = self._settings.repository_token
        if self._settings.repository_token_file is not None:
            environment["WIKI_REPOSITORY_TOKEN_FILE"] = str(
                self._settings.repository_token_file
            )
        if self._ca_bundle is not None:
            environment["GIT_SSL_CAINFO"] = str(self._ca_bundle)
        for proxy_name in ("HTTPS_PROXY", "NO_PROXY", "https_proxy", "no_proxy"):
            if proxy_name in os.environ:
                environment[proxy_name] = os.environ[proxy_name]
        return environment

    def _git(self, arguments: Sequence[str]) -> str:
        command = ("git", *self._GIT_GUARDS, *arguments)
        return self._runner.run(
            command,
            environment=self._environment(),
            timeout_seconds=self._settings.build_timeout_seconds,
        )

    def synchronize(self) -> str:
        """Atualiza uma cópia rasa, valida a árvore e retorna o commit congelado."""

        self._settings.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        Path("/tmp/lucien-git-home").mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise RepositoryError("o workspace do repositório não pode ser um link")
        if not self.path.exists():
            self._initialize_repository()
        if not (self.path / ".git").is_dir() or (self.path / ".git").is_symlink():
            raise RepositoryError("o cache Git local é inválido")

        self._git(("-C", str(self.path), "remote", "set-url", "origin", self._settings.repository_url))
        remote_ref = f"refs/heads/{self._settings.repository_branch}"
        self._git(
            (
                "-C",
                str(self.path),
                "fetch",
                "--force",
                "--prune",
                "--no-tags",
                "--depth=1",
                "origin",
                remote_ref,
            )
        )
        self._git(("-C", str(self.path), "checkout", "--detach", "--force", "FETCH_HEAD"))
        self._git(("-C", str(self.path), "clean", "-ffdx"))
        commit = self._git(("-C", str(self.path), "rev-parse", "--verify", "HEAD"))
        if len(commit) not in (40, 64) or any(character not in "0123456789abcdef" for character in commit):
            raise RepositoryError("o Git retornou um identificador de commit inválido")

        validate_repository_tree(
            self.path,
            max_repository_bytes=self._settings.max_repository_bytes,
            max_source_files=self._settings.max_source_files,
            max_source_bytes=self._settings.max_source_bytes,
            max_file_bytes=self._settings.max_file_bytes,
        )
        docs = self.path / "docs"
        if docs.is_symlink() or not docs.is_dir():
            raise RepositoryError("o repositório não contém um diretório docs regular")
        return commit

    def _initialize_repository(self) -> None:
        candidate = self._settings.workspace / f".repository-{uuid.uuid4().hex}"
        candidate.mkdir(mode=0o700)
        try:
            self._git(("init", "--quiet", str(candidate)))
            self._git(("-C", str(candidate), "remote", "add", "origin", self._settings.repository_url))
            os.replace(candidate, self.path)
        except Exception:
            shutil.rmtree(candidate, ignore_errors=True)
            raise
