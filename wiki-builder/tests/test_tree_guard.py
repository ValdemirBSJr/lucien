from __future__ import annotations

from pathlib import Path

import pytest

from app.tree_guard import TreeValidationError, validate_repository_tree


def validate(root: Path) -> None:
    validate_repository_tree(
        root,
        max_repository_bytes=10_000,
        max_source_files=10,
        max_source_bytes=5_000,
        max_file_bytes=2_000,
    )


def test_arvore_regular_e_aceita(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pack").write_bytes(b"git")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# Runbooks", encoding="utf-8")

    validate(tmp_path)


def test_link_simbolico_e_rejeitado(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    outside = tmp_path / "outside"
    outside.write_text("segredo", encoding="utf-8")
    (tmp_path / "docs" / "escape.md").symlink_to(outside)

    with pytest.raises(TreeValidationError, match="simbólicos"):
        validate(tmp_path)


def test_limite_por_arquivo_e_aplicado(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "large.md").write_bytes(b"x" * 2_001)

    with pytest.raises(TreeValidationError, match="WIKI_MAX_FILE_BYTES"):
        validate(tmp_path)

