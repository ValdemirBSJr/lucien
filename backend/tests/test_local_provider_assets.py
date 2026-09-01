from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domain.ports import AssetToPublish, ConflictError
from app.infrastructure.storage import LocalProvider, playbook_relative_path

_JOB_ID = "1e6a4d1a-9b3e-4c9a-8b0e-2f7a6c9d0e11"
_CREATED_AT = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _provider(tmp_path: Path) -> LocalProvider:
    return LocalProvider(tmp_path)


async def test_publish_with_assets_writes_files_alongside_markdown(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assets = (AssetToPublish(filename="shot.png", content=b"fake-png-bytes"),)

    artifact = await provider.publish(
        _JOB_ID,
        _CREATED_AT,
        "# runbook\n",
        artifact_name="exemplo",
        domain_function="plataforma",
        assets=assets,
    )

    md_relative = playbook_relative_path(_JOB_ID, _CREATED_AT, "exemplo", "plataforma")
    md_path = tmp_path / md_relative
    asset_path = md_path.parent / "assets" / _JOB_ID / "shot.png"

    assert artifact.url == f"local://{md_relative.as_posix()}"
    assert md_path.read_text() == "# runbook\n"
    assert asset_path.read_bytes() == b"fake-png-bytes"


async def test_assets_are_written_before_the_markdown_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(tmp_path)
    assets = (AssetToPublish(filename="shot.png", content=b"fake-png-bytes"),)

    order: list[str] = []
    original_link = __import__("os").link

    def spy_link(source: str, target: str, *args: object, **kwargs: object) -> None:
        order.append(str(target))
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr("os.link", spy_link)

    await provider.publish(
        _JOB_ID,
        _CREATED_AT,
        "# runbook\n",
        artifact_name="exemplo",
        domain_function="plataforma",
        assets=assets,
    )

    assert len(order) == 2
    assert order[0].endswith("shot.png")
    assert order[1].endswith(".md")


async def test_retry_after_partial_asset_write_converges(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    md_relative = playbook_relative_path(_JOB_ID, _CREATED_AT, "exemplo", "plataforma")
    asset_dir = (tmp_path / md_relative).parent / "assets" / _JOB_ID
    asset_dir.mkdir(parents=True)
    (asset_dir / "shot.png").write_bytes(b"fake-png-bytes")  # ja gravado numa tentativa anterior

    assets = (AssetToPublish(filename="shot.png", content=b"fake-png-bytes"),)
    artifact = await provider.publish(
        _JOB_ID,
        _CREATED_AT,
        "# runbook\n",
        artifact_name="exemplo",
        domain_function="plataforma",
        assets=assets,
    )

    assert artifact.url == f"local://{md_relative.as_posix()}"
    assert (tmp_path / md_relative).read_text() == "# runbook\n"


async def test_conflicting_asset_content_raises(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    md_relative = playbook_relative_path(_JOB_ID, _CREATED_AT, "exemplo", "plataforma")
    asset_dir = (tmp_path / md_relative).parent / "assets" / _JOB_ID
    asset_dir.mkdir(parents=True)
    (asset_dir / "shot.png").write_bytes(b"different-bytes-already-there")

    assets = (AssetToPublish(filename="shot.png", content=b"fake-png-bytes"),)
    with pytest.raises(ConflictError):
        await provider.publish(
            _JOB_ID,
            _CREATED_AT,
            "# runbook\n",
            artifact_name="exemplo",
            domain_function="plataforma",
            assets=assets,
        )


async def test_invalid_asset_filename_is_rejected(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assets = (AssetToPublish(filename="../../etc/passwd", content=b"x"),)
    with pytest.raises(ConflictError):
        await provider.publish(
            _JOB_ID,
            _CREATED_AT,
            "# runbook\n",
            artifact_name="exemplo",
            domain_function="plataforma",
            assets=assets,
        )
