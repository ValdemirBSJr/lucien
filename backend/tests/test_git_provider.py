"""Comportamento do provider Git diante de um provedor que não coopera.

O Hub não controla o GitHub nem o Gitea: entre ele e o provedor há proxy,
gateway, DNS e TLS corporativos. Estes testes exercitam o que chega quando algo
nesse caminho falha -- e a garantia é sempre a mesma: a camada de armazenamento
só deixa sair o que a porta promete (`UpstreamError`, `ConflictError`,
`NotFoundError`), nunca uma exceção de transporte crua.
"""

import base64
from datetime import datetime, timezone

import httpx
import pytest

from app.domain.ports import ConflictError, NotFoundError, UpstreamError
from app.infrastructure.storage import GitContentProvider

CRIADO_EM = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
JOB = "11111111-2222-3333-4444-555555555555"
MARKDOWN = "# Runbook\n\nconteúdo publicado\n"


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
    # Mesmo padrão dos testes do SLM: o cliente é criado no primeiro uso, então
    # injetá-lo aqui substitui o transporte sem tocar no resto da construção.
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(responder)
    )
    return provider


def _resposta_de_arquivo(conteudo: str, *, quebrado: bool = False) -> httpx.Response:
    codificado = base64.b64encode(conteudo.encode("utf-8")).decode("ascii")
    if quebrado:
        # GitHub devolve o base64 em linhas de 60 caracteres.
        codificado = "\n".join(
            codificado[i : i + 60] for i in range(0, len(codificado), 60)
        )
    return httpx.Response(
        200,
        json={
            "content": codificado,
            "html_url": "https://git.invalid/infra/runbooks/src/runbook.md",
        },
    )


async def _publicar(provider: GitContentProvider, markdown: str = MARKDOWN):
    return await provider.publish(JOB, CRIADO_EM, markdown, "runbook", "servidores")


async def test_publicacao_de_conteudo_ja_presente_e_idempotente() -> None:
    chamadas: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.method)
        return _resposta_de_arquivo(MARKDOWN)

    provider = _provider(responder)
    try:
        artefato = await _publicar(provider)
        assert artefato.url.endswith("runbook.md")
        # Reconhecer o próprio conteúdo evita o PUT: republicar é comum quando a
        # fila repete, e escrever de novo criaria um commit por tentativa.
        assert chamadas == ["GET"]
    finally:
        await provider.aclose()


async def test_conteudo_remoto_diferente_e_conflito_permanente() -> None:
    provider = _provider(lambda _: _resposta_de_arquivo("# Outro runbook\n"))
    try:
        with pytest.raises(ConflictError):
            await _publicar(provider)
    finally:
        await provider.aclose()


async def test_timeout_no_put_confirma_a_escrita_que_chegou() -> None:
    """O timeout não diz se a escrita chegou; a releitura diz.

    Sem reconciliar, a fila repetiria e a tentativa seguinte encontraria o
    arquivo já publicado -- ou pior, o erro subiria como falha definitiva de um
    Job que na verdade foi publicado.
    """
    estado = {"gravado": False}

    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            # A escrita chegou ao provedor; a resposta é que se perdeu.
            estado["gravado"] = True
            raise httpx.ReadTimeout("resposta não chegou", request=request)
        if estado["gravado"]:
            return _resposta_de_arquivo(MARKDOWN)
        return httpx.Response(404, json={"message": "Not Found"})

    provider = _provider(responder)
    try:
        artefato = await _publicar(provider)
        assert artefato.url.endswith("runbook.md")
    finally:
        await provider.aclose()


async def test_timeout_sem_escrita_vira_erro_da_porta() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            raise httpx.ReadTimeout("sem resposta", request=request)
        return httpx.Response(404, json={"message": "Not Found"})

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError) as capturado:
            await _publicar(provider)
        assert "ReadTimeout" in str(capturado.value)
    finally:
        await provider.aclose()


async def test_falha_de_conexao_na_leitura_vira_erro_da_porta() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await _publicar(provider)
    finally:
        await provider.aclose()


async def test_resposta_que_nao_e_json_vira_erro_da_porta() -> None:
    """Gateway e página de manutenção respondem HTML com status 200."""

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>502 Bad Gateway</html>")

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await _publicar(provider)
    finally:
        await provider.aclose()


async def test_base64_quebrado_em_linhas_e_aceito() -> None:
    """O formato do GitHub precisa continuar passando."""
    provider = _provider(lambda _: _resposta_de_arquivo(MARKDOWN, quebrado=True))
    try:
        lido = await provider.read_published(JOB, CRIADO_EM, "runbook", "servidores")
        assert lido == MARKDOWN
    finally:
        await provider.aclose()


async def test_base64_corrompido_nao_vira_artefato_plausivel() -> None:
    """Descartar caractere fora do alfabeto devolve bytes plausíveis.

    Era o comportamento de `validate=False`. A cadeia abaixo tem lixo no meio e
    mesmo assim decodificava, em silêncio, para `# Runbook\\n\\nconteudo\\n` --
    texto que passaria por runbook e seria servido como o artefato publicado.
    """
    corrompido = "IyBSdW5ib29r!!!Cgpjb250ZXVkbwo="
    assert base64.b64decode(corrompido, validate=False) == b"# Runbook\n\nconteudo\n"

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": corrompido})

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError):
            await provider.read_published(JOB, CRIADO_EM, "runbook", "servidores")
    finally:
        await provider.aclose()


async def test_conteudo_ausente_no_json_vira_erro_da_porta() -> None:
    provider = _provider(lambda _: httpx.Response(200, json={"html_url": "x"}))
    try:
        with pytest.raises(UpstreamError):
            await provider.read_published(JOB, CRIADO_EM, "runbook", "servidores")
    finally:
        await provider.aclose()


async def test_leitura_de_artefato_ausente_em_todos_os_layouts() -> None:
    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    provider = _provider(responder)
    try:
        with pytest.raises(NotFoundError):
            await provider.read_published(JOB, CRIADO_EM, "runbook", "servidores")
    finally:
        await provider.aclose()


async def test_recusa_do_provedor_preserva_o_status() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(403, json={"message": "pre-receive hook declined"})
        return httpx.Response(404, json={"message": "Not Found"})

    provider = _provider(responder)
    try:
        with pytest.raises(UpstreamError) as capturado:
            await _publicar(provider)
        assert "403" in str(capturado.value)
    finally:
        await provider.aclose()


async def test_aclose_permite_reabertura() -> None:
    """Fechar não pode deixar o provider inutilizável."""
    provider = _provider(lambda _: _resposta_de_arquivo(MARKDOWN))
    await provider.aclose()
    assert provider._client is None
    # Sem transporte injetado o cliente novo tentaria a rede de verdade; basta
    # saber que ele é criado de novo em vez de estourar.
    assert provider._cliente() is not None
    await provider.aclose()
