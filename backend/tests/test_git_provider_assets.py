"""Publicação de Markdown + imagem via Git Data API (commit atômico).

A Contents API só publica um arquivo por commit (ver test_git_provider.py).
Estes testes cobrem o caminho novo, exercitado só quando `assets` não é
vazio: blobs -> tree -> commit -> PATCH de ref, com o mesmo vocabulário de
erro (`UpstreamError`/`ConflictError`) que o caminho de arquivo único já usa.
"""

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.domain.ports import AssetToPublish, ConflictError, UpstreamError
from app.infrastructure.storage import GitContentProvider, GiteaProvider

CRIADO_EM = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
JOB = "11111111-2222-3333-4444-555555555555"
MARKDOWN = "# Runbook\n\nconteúdo publicado\n"
ASSET = AssetToPublish(filename="abc123.png", content=b"fake-png")


def _provider(responder) -> GitContentProvider:
    provider = GitContentProvider(
        api_base="https://git.invalid/api/v1",
        owner="infra",
        repository="runbooks",
        branch="main",
        token="token-de-teste",
        authorization_scheme="token",
        docs_prefix="docs",
        ca_file=None,
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    return provider


def _gitea_provider(responder) -> GiteaProvider:
    # GiteaProvider.__init__ só aceita Settings; nos testes construímos como o
    # GitContentProvider genérico, chamando o __init__ da base diretamente --
    # mesmo truque que `_provider()` já usa para a classe base.
    provider = GiteaProvider.__new__(GiteaProvider)
    GitContentProvider.__init__(
        provider,
        api_base="https://git.invalid/api/v1",
        owner="infra",
        repository="runbooks",
        branch="main",
        token="token-de-teste",
        authorization_scheme="token",
        docs_prefix="docs",
        ca_file=None,
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    return provider


async def _publish(provider: GitContentProvider, markdown: str = MARKDOWN):
    return await provider.publish(
        JOB, CRIADO_EM, markdown, "runbook", "servidores", assets=(ASSET,)
    )


def _happy_path_responder(calls: list[tuple[str, str]]):
    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
        if request.method == "GET" and path.endswith("/git/commits/base-commit-sha"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
        if request.method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": f"blob-sha-{len(calls)}"})
        if request.method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"})
        if request.method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"})
        if request.method == "PATCH" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"ref": "refs/heads/main"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    return responder


async def test_publish_with_assets_uses_blob_tree_commit_ref_sequence() -> None:
    calls: list[tuple[str, str]] = []
    provider = _provider(_happy_path_responder(calls))
    try:
        artefato = await _publish(provider)
        # O caminho com assets nao busca um html_url mais bonito depois do
        # PATCH de ref -- devolve a URL da Contents API, que ja identifica
        # corretamente onde o artefato foi publicado.
        assert artefato.url.endswith(f"runbook--{JOB}.md")
        methods = [method for method, _ in calls]
        # 1 GET (contents, ja existe?) + 1 GET (ref) + 1 GET (commit base) +
        # 2 POST (blob markdown, blob asset) + 1 POST (tree) + 1 POST (commit)
        # + 1 PATCH (ref) -- um unico ponto de ativacao atomica no final.
        assert methods == ["GET", "GET", "GET", "POST", "POST", "POST", "POST", "PATCH"]
    finally:
        await provider.aclose()


async def test_tree_entries_cover_markdown_and_asset_paths() -> None:
    calls: list[tuple[str, str]] = []
    captured_tree_payload: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
        if request.method == "GET" and path.endswith("/git/commits/base-commit-sha"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
        if request.method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": f"blob-sha-{len(calls)}"})
        if request.method == "POST" and path.endswith("/git/trees"):
            captured_tree_payload.update(json.loads(request.content))
            return httpx.Response(201, json={"sha": "new-tree-sha"})
        if request.method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"})
        if request.method == "PATCH" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"ref": "refs/heads/main"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _provider(responder)
    try:
        await _publish(provider)
        assert captured_tree_payload["base_tree"] == "base-tree-sha"
        paths = {entry["path"] for entry in captured_tree_payload["tree"]}
        assert "docs/2026/servidores/runbook--11111111-2222-3333-4444-555555555555.md" in paths
        assert (
            "docs/2026/servidores/assets/11111111-2222-3333-4444-555555555555/abc123.png"
            in paths
        )
    finally:
        await provider.aclose()


async def test_publish_with_assets_already_published_is_idempotent() -> None:
    calls: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        encoded = base64.b64encode(MARKDOWN.encode("utf-8")).decode("ascii")
        return httpx.Response(
            200,
            json={
                "content": encoded,
                "html_url": "https://git.invalid/infra/runbooks/src/runbook.md",
            },
        )

    provider = _provider(responder)
    try:
        artefato = await _publish(provider)
        assert artefato.url.endswith("runbook.md")
        # So a leitura inicial: conteudo identico ja publicado nao cria blob/commit novo.
        assert calls == ["GET"]
    finally:
        await provider.aclose()


async def test_non_fast_forward_ref_update_reconciles_or_conflicts() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
        if request.method == "GET" and path.endswith("/git/commits/base-commit-sha"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
        if request.method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-sha"})
        if request.method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"})
        if request.method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"})
        if request.method == "PATCH" and path.endswith("/git/refs/heads/main"):
            # Outra publicacao moveu o branch primeiro.
            return httpx.Response(422, json={"message": "not a fast-forward"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _provider(responder)
    try:
        with pytest.raises(ConflictError):
            await _publish(provider)
    finally:
        await provider.aclose()


async def test_transport_failure_on_ref_update_becomes_upstream_error() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit-sha"}})
        if request.method == "GET" and path.endswith("/git/commits/base-commit-sha"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
        if request.method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-sha"})
        if request.method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"})
        if request.method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"})
        if request.method == "PATCH" and path.endswith("/git/refs/heads/main"):
            raise httpx.ConnectError("conexao recusada", request=request)
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await _publish(provider)
    finally:
        await provider.aclose()


async def test_malformed_upstream_response_becomes_upstream_error() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            # Falta o campo "object.sha" esperado.
            return httpx.Response(200, json={"unexpected": "shape"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await _publish(provider)
    finally:
        await provider.aclose()


async def test_gitea_publica_com_anexo_pelo_endpoint_de_contents_em_lote() -> None:
    """Gitea não tem blob->tree->commit->ref -- usa POST .../contents com um
    array `files`, resolvendo o commit atômico num único request."""

    calls: list[tuple[str, str]] = []
    captured_payload: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and path.endswith("/contents"):
            captured_payload.update(json.loads(request.content))
            return httpx.Response(201, json={"commit": {"sha": "novo-commit"}})
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _gitea_provider(responder)
    try:
        artefato = await _publish(provider)
        assert artefato.url.endswith(f"runbook--{JOB}.md")
        # So a leitura inicial (contents, ja existe?) + o POST em lote -- nada
        # de git/refs, git/commits ou git/blobs, que o Gitea nao tem.
        assert [method for method, _ in calls] == ["GET", "POST"]
        assert captured_payload["branch"] == "main"
        arquivos = captured_payload["files"]
        caminhos = {item["path"] for item in arquivos}
        assert (
            "docs/2026/servidores/runbook--11111111-2222-3333-4444-555555555555.md"
            in caminhos
        )
        assert (
            "docs/2026/servidores/assets/11111111-2222-3333-4444-555555555555/abc123.png"
            in caminhos
        )
        assert all(item["operation"] == "create" for item in arquivos)
    finally:
        await provider.aclose()


async def test_gitea_conflito_no_contents_em_lote_reconcilia_ou_recusa() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and path.endswith("/contents"):
            return httpx.Response(409, json={"message": "conflict"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _gitea_provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await _publish(provider)
    finally:
        await provider.aclose()


async def test_gitea_ref_read_devolve_lista_em_vez_de_objeto() -> None:
    """Regressão: Gitea recusa a forma singular (git/ref/) com 404 e só
    responde à plural (git/refs/) -- mas devolve uma *lista*, não um objeto
    único como o GitHub. Publicar com anexo precisa funcionar nos dois."""

    calls: list[tuple[str, str]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/ref/heads/main"):
            # A forma singular nunca deveria ser chamada.
            raise AssertionError("a forma singular não deveria ser usada")
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(
                200,
                json=[
                    {"ref": "refs/heads/other", "object": {"sha": "sha-de-outro-branch"}},
                    {"ref": "refs/heads/main", "object": {"sha": "base-commit-sha"}},
                ],
            )
        if request.method == "GET" and path.endswith("/git/commits/base-commit-sha"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
        if request.method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": f"blob-sha-{len(calls)}"})
        if request.method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"})
        if request.method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"})
        if request.method == "PATCH" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={"ref": "refs/heads/main"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _provider(responder)
    try:
        artefato = await _publish(provider)
        assert artefato.url.endswith(f"runbook--{JOB}.md")
    finally:
        await provider.aclose()


async def test_branch_ausente_na_lista_do_gitea_vira_upstream_error() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and path.endswith("/git/refs/heads/main"):
            return httpx.Response(
                200, json=[{"ref": "refs/heads/other", "object": {"sha": "x"}}]
            )
        raise AssertionError(f"unexpected request {request.method} {path}")

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await _publish(provider)
    finally:
        await provider.aclose()
