from __future__ import annotations

from pathlib import Path

import pytest

from app.publication import AtomicPublisher


COMMIT_A = "a" * 40 + "-" + "1" * 12
COMMIT_B = "b" * 40 + "-" + "1" * 12


def write_site(destination: Path, text: str) -> None:
    (destination / "index.html").write_text(text, encoding="utf-8")


def test_retry_da_mesma_release_e_idempotente(tmp_path: Path) -> None:
    publisher = AtomicPublisher(tmp_path, retention=3, max_site_bytes=10_000)
    calls = 0

    def build(destination: Path) -> None:
        nonlocal calls
        calls += 1
        write_site(destination, "primeira")

    with publisher.lock():
        first = publisher.publish(COMMIT_A, build)
        second = publisher.publish(COMMIT_A, build)

    assert first == second
    assert calls == 1
    assert (tmp_path / "current").resolve() == first.resolve()


def test_falha_preserva_release_atual(tmp_path: Path) -> None:
    publisher = AtomicPublisher(tmp_path, retention=3, max_site_bytes=10_000)
    with publisher.lock():
        first = publisher.publish(COMMIT_A, lambda destination: write_site(destination, "ok"))

        def fail(_: Path) -> None:
            raise RuntimeError("falha simulada")

        with pytest.raises(RuntimeError, match="simulada"):
            publisher.publish(COMMIT_B, fail)

    assert (tmp_path / "current").resolve() == first.resolve()
    assert (tmp_path / "current" / "index.html").read_text(encoding="utf-8") == "ok"
    assert not (tmp_path / "releases" / COMMIT_B).exists()


def test_retentao_remove_apenas_releases_antigas(tmp_path: Path) -> None:
    publisher = AtomicPublisher(tmp_path, retention=2, max_site_bytes=10_000)
    releases = [f"{character * 40}-{'1' * 12}" for character in "abc"]
    with publisher.lock():
        for release in releases:
            publisher.publish(release, lambda destination, r=release: write_site(destination, r))

    present = {path.name for path in (tmp_path / "releases").iterdir()}
    assert present == set(releases[-2:])
    assert (tmp_path / "current").resolve().name == releases[-1]

