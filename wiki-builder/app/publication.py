"""Build determinístico e promoção atômica de releases estáticas."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .tree_guard import validate_site_tree

_LOG = logging.getLogger("lucien.wiki_builder")
# Um build quebrado nao pode virar firehose: o ciclo repete a cada
# WIKI_POLL_SECONDS e a saida inteira do MkDocs iria junto toda vez.
_MAX_OUTPUT_LINES = 20
_MAX_SUMMARY_CHARS = 200


class BuildError(RuntimeError):
    """Falha fechada durante build ou publicação."""


_RELEASE_ID = re.compile(r"^[0-9a-f]{40,64}-[0-9a-f]{12}$")


def builder_fingerprint(files: Sequence[Path]) -> str:
    """Muda o artefato quando qualquer entrada confiável do builder muda."""

    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: str(item)):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _decode(saida: bytes | str | None) -> str:
    if saida is None:
        return ""
    if isinstance(saida, str):
        return saida
    # A saída vem do conteúdo do repositório; um byte inválido não pode
    # derrubar o relato da falha que estamos tentando explicar.
    return saida.decode("utf-8", errors="replace")


class MkDocsBuilder:
    """Invoca exclusivamente a configuração incorporada à imagem."""

    def __init__(self, *, timeout_seconds: int, config_file: Path = Path("/app/mkdocs.yml")) -> None:
        self._timeout_seconds = timeout_seconds
        self._config_file = config_file

    def build(self, docs_dir: Path, site_dir: Path) -> None:
        if docs_dir.is_symlink() or not docs_dir.is_dir():
            raise BuildError("o diretório docs é inválido")
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "LUCIEN_DOCS_DIR": str(docs_dir),
            "LUCIEN_SITE_DIR": str(site_dir),
        }
        try:
            result = subprocess.run(
                (
                    sys.executable,
                    "-m",
                    "mkdocs",
                    "build",
                    "--strict",
                    "--config-file",
                    str(self._config_file),
                ),
                cwd="/app",
                env=environment,
                stdin=subprocess.DEVNULL,
                # Descartar a saída deixava a falha indiagnosticável: o log só
                # dizia "status N", e o operador não tinha por onde começar.
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._report(_decode(exc.output), "tempo esgotado")
            raise BuildError(
                f"o MkDocs não concluiu em {self._timeout_seconds}s"
            ) from exc
        except OSError as exc:
            # Nada a ver com timeout: o processo nem chegou a iniciar.
            raise BuildError(f"o MkDocs não pôde ser executado: {exc}") from exc
        if result.returncode != 0:
            resumo = self._report(
                _decode(result.stdout), f"status {result.returncode}"
            )
            raise BuildError(
                f"o MkDocs encerrou com status {result.returncode}: {resumo}"
            )

    def _report(self, saida: str, motivo: str) -> str:
        """Registra o rabo da saída e devolve uma linha para a exceção.

        A mensagem curta acompanha o erro do ciclo, que já é uma linha só; o
        bloco detalhado vai para o log separadamente, para não transformar
        cada falha numa mensagem de vinte linhas.
        """

        linhas = [linha for linha in saida.splitlines() if linha.strip()]
        if not linhas:
            return "sem saída do MkDocs"
        rabo = linhas[-_MAX_OUTPUT_LINES:]
        _LOG.error(
            "MkDocs falhou (%s); ultimas %d linha(s) da saida:\n%s",
            motivo,
            len(rabo),
            "\n".join(rabo),
        )
        return rabo[-1][:_MAX_SUMMARY_CHARS]


class AtomicPublisher:
    """Mantém releases imutáveis e troca somente o link `current`."""

    def __init__(self, root: Path, *, retention: int, max_site_bytes: int) -> None:
        self.root = root
        self.releases = root / "releases"
        self.retention = retention
        self.max_site_bytes = max_site_bytes

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.root / ".builder.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def publish(self, release_id: str, build: Callable[[Path], None]) -> Path:
        if not _RELEASE_ID.fullmatch(release_id):
            raise BuildError("identificador de release inválido")
        self.releases.mkdir(parents=True, exist_ok=True, mode=0o755)
        self._remove_abandoned_candidates()
        release = self.releases / release_id

        if self._is_complete_release(release):
            self._promote(release)
            self._apply_retention(release)
            return release
        if release.exists() or release.is_symlink():
            raise BuildError("a release existente está incompleta")

        candidate = self.root / f".candidate-{release_id}-{uuid.uuid4().hex}"
        candidate.mkdir(mode=0o755)
        try:
            build(candidate)
            validate_site_tree(candidate, max_site_bytes=self.max_site_bytes)
            marker = candidate / ".complete"
            marker.write_text(f"{release_id}\n", encoding="ascii")
            self._fsync_tree(candidate)
            os.replace(candidate, release)
            self._fsync_directory(self.releases)
        except Exception:
            shutil.rmtree(candidate, ignore_errors=True)
            raise

        self._promote(release)
        self._apply_retention(release)
        return release

    def record_health(self, *, commit: str, release_id: str) -> None:
        payload = {
            "commit": commit,
            "release": release_id,
            "updated_at": time.time(),
        }
        candidate = self.root / f".health-{uuid.uuid4().hex}.tmp"
        candidate.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        candidate.chmod(0o600)
        with candidate.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(candidate, self.root / ".builder-health.json")
        self._fsync_directory(self.root)

    def healthy(self, *, maximum_age_seconds: int) -> bool:
        health = self.root / ".builder-health.json"
        current = self.root / "current"
        try:
            if health.is_symlink() or not health.is_file() or not current.is_symlink():
                return False
            payload = json.loads(health.read_text(encoding="utf-8"))
            updated_at = float(payload["updated_at"])
            if time.time() - updated_at > maximum_age_seconds or updated_at > time.time() + 30:
                return False
            target = current.readlink()
            if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != "releases":
                return False
            return self._is_complete_release(self.root / target)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False

    def _promote(self, release: Path) -> None:
        relative_target = Path("releases") / release.name
        candidate_link = self.root / f".current-{uuid.uuid4().hex}"
        try:
            candidate_link.symlink_to(relative_target, target_is_directory=True)
            os.replace(candidate_link, self.root / "current")
            self._fsync_directory(self.root)
        finally:
            candidate_link.unlink(missing_ok=True)

    def _apply_retention(self, current: Path) -> None:
        complete = [
            entry
            for entry in self.releases.iterdir()
            if entry.is_dir() and not entry.is_symlink() and self._is_complete_release(entry)
        ]
        complete.sort(key=lambda entry: entry.stat().st_mtime_ns, reverse=True)
        keep = {current}
        for release in complete:
            if len(keep) >= self.retention:
                break
            keep.add(release)
        for old_release in complete:
            if old_release not in keep:
                shutil.rmtree(old_release)

    def _remove_abandoned_candidates(self) -> None:
        for candidate in self.root.glob(".candidate-*"):
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink(missing_ok=True)
            elif candidate.is_dir():
                shutil.rmtree(candidate)

    @staticmethod
    def _is_complete_release(path: Path) -> bool:
        return (
            path.is_dir()
            and not path.is_symlink()
            and (path / ".complete").is_file()
            and not (path / ".complete").is_symlink()
            and (path / "index.html").is_file()
            and not (path / "index.html").is_symlink()
        )

    @staticmethod
    def _fsync_tree(root: Path) -> None:
        for directory, _, filenames in os.walk(root, followlinks=False):
            for filename in filenames:
                path = Path(directory) / filename
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            AtomicPublisher._fsync_directory(Path(directory))

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
