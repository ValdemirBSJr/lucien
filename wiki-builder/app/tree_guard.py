"""Validação defensiva das árvores recebidas e geradas."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class TreeValidationError(RuntimeError):
    """Indica árvore insegura ou maior que os limites administrativos."""


@dataclass(frozen=True, slots=True)
class TreeSummary:
    files: int
    source_bytes: int
    repository_bytes: int


def validate_repository_tree(
    root: Path,
    *,
    max_repository_bytes: int,
    max_source_files: int,
    max_source_bytes: int,
    max_file_bytes: int,
) -> TreeSummary:
    """Percorre sem seguir links e aplica limites antes do MkDocs."""

    if root.is_symlink() or not root.is_dir():
        raise TreeValidationError("a raiz do repositório não é um diretório regular")

    source_files = 0
    source_bytes = 0
    repository_bytes = 0
    pending: list[tuple[Path, bool]] = [(root, False)]

    while pending:
        directory, inside_git = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise TreeValidationError("não foi possível inspecionar o repositório") from exc

        for entry in entries:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise TreeValidationError("não foi possível inspecionar uma entrada") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise TreeValidationError("links simbólicos não são aceitos")

            entry_inside_git = inside_git or (directory == root and entry.name == ".git")
            if stat.S_ISDIR(metadata.st_mode):
                pending.append((Path(entry.path), entry_inside_git))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise TreeValidationError("apenas arquivos e diretórios regulares são aceitos")

            repository_bytes += metadata.st_size
            if repository_bytes > max_repository_bytes:
                raise TreeValidationError("o repositório excede WIKI_MAX_REPOSITORY_BYTES")
            if entry_inside_git:
                continue

            source_files += 1
            source_bytes += metadata.st_size
            if source_files > max_source_files:
                raise TreeValidationError("o repositório excede WIKI_MAX_SOURCE_FILES")
            if source_bytes > max_source_bytes:
                raise TreeValidationError("o conteúdo excede WIKI_MAX_SOURCE_BYTES")
            if metadata.st_size > max_file_bytes:
                raise TreeValidationError("um arquivo excede WIKI_MAX_FILE_BYTES")

    return TreeSummary(source_files, source_bytes, repository_bytes)


def validate_site_tree(root: Path, *, max_site_bytes: int) -> None:
    """Garante que o artefato contém apenas arquivos regulares e possui índice."""

    if root.is_symlink() or not root.is_dir() or not (root / "index.html").is_file():
        raise TreeValidationError("o build não produziu um site válido")

    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in os.scandir(directory):
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise TreeValidationError("o site gerado contém link simbólico")
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
                if total > max_site_bytes:
                    raise TreeValidationError("o site excede WIKI_MAX_SITE_BYTES")
            else:
                raise TreeValidationError("o site contém uma entrada especial")

