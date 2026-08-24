"""Entrypoint do builder contínuo e dos modos de operação pontuais."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path

from .git_repository import GitRepository, RepositoryError
from .index_page import ensure_index
from .publication import (
    AtomicPublisher,
    BuildError,
    MkDocsBuilder,
    builder_fingerprint,
)
from .settings import Settings, SettingsError
from .tree_guard import TreeValidationError

_LOG = logging.getLogger("lucien.wiki_builder")
_APP_ROOT = Path("/app")

# Entradas que determinam o site gerado: mudar qualquer uma precisa produzir
# outra release, senao o publicado ficaria preso a uma versao anterior do
# builder. `requirements.lock` e mais preciso que o requirements.txt que estava
# aqui antes -- ele fixa tambem as transitivas, que sao parte do que compila.
#
# Esta lista e lida em tempo de execucao. `tests/test_imagem.py` confere que o
# Dockerfile leva todos estes arquivos para dentro da imagem.
ARQUIVOS_DA_IMPRESSAO = (
    Path("mkdocs.yml"),
    Path("requirements.lock"),
    Path("app/mkdocs_hook.py"),
    Path("app/index_page.py"),
)


class WikiBuilderService:
    """Orquestra portas concretas sem atribuir autoridade ao repositório."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repository = GitRepository(settings)
        self._builder = MkDocsBuilder(timeout_seconds=settings.build_timeout_seconds)
        self._publisher = AtomicPublisher(
            settings.publish_root,
            retention=settings.release_retention,
            max_site_bytes=settings.max_site_bytes,
        )
        self._fingerprint = builder_fingerprint(
            tuple(_APP_ROOT / relativo for relativo in ARQUIVOS_DA_IMPRESSAO)
        )

    def run_once(self) -> str:
        with self._publisher.lock():
            commit = self._repository.synchronize()
            # Depois da validacao da arvore e antes do build: o MkDocs precisa
            # de um index.md na raiz para produzir o index.html que
            # `validate_site_tree` exige.
            ensure_index(
                self._repository.path / "docs",
                self._settings.domain_functions,
            )
            release_id = f"{commit}-{self._fingerprint}"
            self._publisher.publish(
                release_id,
                lambda destination: self._builder.build(
                    self._repository.path / "docs", destination
                ),
            )
            self._publisher.record_health(commit=commit, release_id=release_id)
            return release_id

    def healthy(self) -> bool:
        return self._publisher.healthy(
            maximum_age_seconds=max(120, self._settings.poll_seconds * 3)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Builder seguro da wiki Lucien")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="sincroniza e compila uma vez")
    mode.add_argument(
        "--healthcheck", action="store_true", help="valida a última publicação"
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.from_environment()
        service = WikiBuilderService(settings)
    except (SettingsError, RepositoryError, OSError) as exc:
        _LOG.error("configuração inválida do builder: %s", exc)
        return 2

    if arguments.healthcheck:
        return 0 if service.healthy() else 1
    if arguments.once:
        try:
            release = service.run_once()
            _LOG.info("release publicada: %s", release)
            return 0
        except (BuildError, RepositoryError, TreeValidationError, OSError) as exc:
            _LOG.error("publicação não concluída: %s", exc)
            return 1

    stopped = threading.Event()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            release = service.run_once()
            _LOG.info("release reconciliada: %s", release)
        except (BuildError, RepositoryError, TreeValidationError, OSError) as exc:
            _LOG.error("ciclo de publicação falhou; a release anterior foi preservada: %s", exc)
        stopped.wait(settings.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

