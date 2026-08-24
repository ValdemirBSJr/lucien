from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_limite_do_portal_nao_excede_catalogo_do_hub() -> None:
    with pytest.raises(ValidationError, match="VIEWER_MAX_DOCUMENTS"):
        Settings(
            viewer_session_secret="segredo-de-teste-com-mais-de-32-bytes",  # gitleaks:allow
            viewer_max_documents=10_001,
        )


def test_segredo_de_sessao_pode_ser_lido_de_arquivo(tmp_path: Path) -> None:
    secret_file = tmp_path / "viewer_session_secret"
    secret_file.write_text("s" * 32, encoding="utf-8")

    settings = Settings(viewer_session_secret_file=secret_file)

    assert settings.viewer_session_secret.get_secret_value() == "s" * 32
