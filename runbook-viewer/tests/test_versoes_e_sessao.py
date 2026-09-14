import base64
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import SESSION_COOKIE, create_app
from app.models import AuthenticatedUser
from app.repository import RunbookRepository
from app.security import InvalidCredentialsError, SessionCipher, SessionCredential


TOKEN = "luc_token_valido_123456"
SEGREDO = "segredo-de-sessao-com-mais-de-32-bytes-aleatorios"
ORDEM_DO_HUB = [
    "id",
    "runbook_raiz",
    "revisao",
    "substitui",
    "autor",
    "nivel_autor",
    "funcao",
    "data_criacao",
    "tags_inferidas",
    "versao",
    "ultimo_revisor",
    "data_revisao",
]


class _Hub:
    def __init__(self, published_ids: set[str], role: str = "senior") -> None:
        self.published_ids = frozenset(published_ids)
        self.role = role

    async def verify(self, username: str, token: str) -> AuthenticatedUser:
        if username != "operador" or token != TOKEN:
            raise InvalidCredentialsError
        return AuthenticatedUser("user-1", "operador", self.role, "servidores")

    async def list_published_ids(self, token: str) -> frozenset[str]:
        return self.published_ids

    async def create_revision(self, *_: str) -> None:
        raise AssertionError("o portal não deveria revisar nestes testes")


def _settings(root: Path) -> Settings:
    return Settings(
        viewer_hub_url="https://hub:8443",
        viewer_hub_ca_file=root / "ca.crt",
        viewer_session_secret=SecretStr(SEGREDO),
        viewer_runbooks_root=root,
        viewer_session_ttl_seconds=600,
        viewer_session_max_seconds=28_800,
        viewer_max_documents=100,
        viewer_max_file_bytes=1024 * 1024,
    )


def _publicar(
    root: Path,
    runbook_id: str,
    titulo: str,
    *,
    raiz: str | None = None,
    anterior: str | None = None,
    revisao: int = 1,
) -> None:
    """Grava no formato atual do Hub: a mesma ordem de chaves do frontmatter."""

    linhagem = (
        f'runbook_raiz: "{raiz}"\nrevisao: {revisao}\nsubstitui: "{anterior}"\n'
        if raiz
        else ""
    )
    revisor = '"admin - Revisora"' if raiz else '""'
    data_revisao = '"2026-09-13T15:00:00Z"' if raiz else '""'
    alvo = root / "2026" / "servidores" / f"{runbook_id}.md"
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(
        "---\n"
        f'id: "{runbook_id}"\n'
        f"{linhagem}"
        'autor: "operador - Operador Um"\n'
        'nivel_autor: "senior"\n'
        'funcao: "servidores"\n'
        'data_criacao: "2026-09-13T12:00:00Z"\n'
        'tags_inferidas: ["linux", "disk"]\n'
        f'versao: "{revisao}"\n'
        f"ultimo_revisor: {revisor}\n"
        f"data_revisao: {data_revisao}\n"
        "---\n"
        f"# {titulo}\n\n### Step 1: Check\n```bash\ndf -h /\n```\n",
        encoding="utf-8",
    )


def _cadeia(root: Path) -> list[str]:
    """Três versões publicadas do mesmo runbook: v1 (raiz), v2 e v3."""

    ids = [str(uuid4()) for _ in range(3)]
    _publicar(root, ids[0], "Versao 1")
    _publicar(root, ids[1], "Versao 2", raiz=ids[0], anterior=ids[0], revisao=2)
    _publicar(root, ids[2], "Versao 3", raiz=ids[0], anterior=ids[1], revisao=3)
    return ids


def _cliente(root: Path, published: set[str]) -> TestClient:
    app = create_app(
        _settings(root), _Hub(published), RunbookRepository(root, 100, 1024 * 1024, 60)
    )
    return TestClient(app, base_url="https://viewer.test")


def _login(client: TestClient) -> None:
    pagina = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', pagina.text)
    assert csrf is not None
    resposta = client.post(
        "/login",
        data={"username": "operador", "api_token": TOKEN, "csrf_token": csrf.group(1)},
        follow_redirects=False,
    )
    assert resposta.status_code == 303


def _cookies_de_sessao(resposta: object) -> list[str]:
    return [
        valor
        for valor in resposta.headers.get_list("set-cookie")  # type: ignore[attr-defined]
        if valor.startswith(f"{SESSION_COOKIE}=")
    ]


# --- versões -----------------------------------------------------------------


def test_pagina_da_raiz_mostra_a_atual_e_o_historico(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, set(ids)) as client:
        _login(client)
        pagina = client.get(f"/runbooks/{ids[0]}")

    assert pagina.status_code == 200
    assert "Versao 3" in pagina.text
    assert f"/runbooks/{ids[0]}/edit" in pagina.text
    assert f"/runbooks/{ids[0]}/versions/1" in pagina.text
    assert f"/runbooks/{ids[0]}/versions/2" in pagina.text
    assert 'aria-current="page">v3 · current' in pagina.text
    assert "version-banner" not in pagina.text


def test_versao_antiga_abre_so_para_leitura(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, set(ids)) as client:
        _login(client)
        pagina = client.get(f"/runbooks/{ids[0]}/versions/1")

    assert pagina.status_code == 200
    assert "Versao 1" in pagina.text
    assert "Versao 3" not in pagina.text
    assert "You are viewing version 1 of 3" in pagina.text
    assert "/edit" not in pagina.text
    assert f'href="/runbooks/{ids[0]}">v3 · current' in pagina.text


def test_versao_atual_pelo_numero_redireciona_para_a_raiz(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, set(ids)) as client:
        _login(client)
        resposta = client.get(f"/runbooks/{ids[0]}/versions/3", follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/runbooks/{ids[0]}"


def test_versao_inexistente_ou_por_id_de_versao_e_404(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, set(ids)) as client:
        _login(client)
        assert client.get(f"/runbooks/{ids[0]}/versions/4").status_code == 404
        assert client.get(f"/runbooks/{ids[0]}/versions/0").status_code == 404
        # O endereço é sempre a raiz: o id da v2 sozinho não abre nada.
        assert client.get(f"/runbooks/{ids[1]}/versions/1").status_code == 404


def test_versao_nao_publicada_nao_aparece_no_historico(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, {ids[0], ids[1]}) as client:
        _login(client)
        pagina = client.get(f"/runbooks/{ids[0]}")

    assert "Versao 2" in pagina.text
    assert "v3" not in pagina.text


def test_edicao_continua_so_na_versao_atual(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, set(ids)) as client:
        _login(client)
        edicao = client.get(f"/runbooks/{ids[0]}/edit")

    assert edicao.status_code == 200
    assert "# Versao 3" in edicao.text
    assert "keepalive.js" in edicao.text


def test_tabela_tem_todo_o_frontmatter_na_ordem_do_arquivo(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    with _cliente(tmp_path, set(ids)) as client:
        _login(client)
        pagina = client.get(f"/runbooks/{ids[0]}/versions/2")

    assert re.findall(r'<th scope="col">([^<]+)</th>', pagina.text) == ORDEM_DO_HUB
    celulas = re.findall(r"<td>([^<]*)</td>", pagina.text)
    assert celulas[ORDEM_DO_HUB.index("id")] == ids[1]
    assert celulas[ORDEM_DO_HUB.index("substitui")] == ids[0]
    assert celulas[ORDEM_DO_HUB.index("tags_inferidas")] == "linux, disk"
    assert celulas[ORDEM_DO_HUB.index("ultimo_revisor")] == "admin - Revisora"


def test_primeira_versao_mostra_as_colunas_de_revisao_vazias(tmp_path: Path) -> None:
    runbook_id = str(uuid4())
    _publicar(tmp_path, runbook_id, "Sozinho")
    with _cliente(tmp_path, {runbook_id}) as client:
        _login(client)
        pagina = client.get(f"/runbooks/{runbook_id}")

    chaves = re.findall(r'<th scope="col">([^<]+)</th>', pagina.text)
    assert chaves == [chave for chave in ORDEM_DO_HUB if chave not in {"runbook_raiz", "revisao", "substitui"}]
    assert "version-history" not in pagina.text


@pytest.mark.asyncio
async def test_repositorio_devolve_a_versao_pedida_e_a_cadeia(tmp_path: Path) -> None:
    ids = _cadeia(tmp_path)
    repository = RunbookRepository(tmp_path, 100, 1024 * 1024, cache_ttl_seconds=0)
    publicados = frozenset(ids)

    atual = await repository.get_runbook(ids[0], publicados)
    primeira = await repository.get_runbook(ids[0], publicados, 1)

    assert atual is not None and primeira is not None
    assert atual.summary.id == ids[2] and atual.is_latest
    assert primeira.summary.id == ids[0] and not primeira.is_latest
    assert [versao.id for versao in primeira.versions] == ids
    assert await repository.get_runbook(ids[0], publicados, 4) is None
    # A lista continua com uma entrada por runbook: a atual.
    assert [item.id for item in await repository.list_runbooks(publicados)] == [ids[2]]


# --- sessão ------------------------------------------------------------------


class _Relogio:
    def __init__(self) -> None:
        self.agora = 1_800_000_000.0

    def __call__(self) -> float:
        return self.agora


def test_sessao_cai_depois_de_dez_minutos_sem_uso() -> None:
    relogio = _Relogio()
    cipher = SessionCipher(SEGREDO, 600, 28_800, relogio)
    cookie = cipher.seal(SessionCredential("operador", TOKEN))

    relogio.agora += 599
    assert cipher.open(cookie).username == "operador"
    relogio.agora += 2
    with pytest.raises(InvalidCredentialsError):
        cipher.open(cookie)


def test_uso_continuo_renova_mas_respeita_o_teto() -> None:
    relogio = _Relogio()
    cipher = SessionCipher(SEGREDO, 600, 28_800, relogio)
    cookie = cipher.seal(SessionCredential("operador", TOKEN))
    login = relogio.agora

    # Uma página a cada cinco minutos: a sessão nunca fica parada dez.
    while relogio.agora + 300 - login <= 28_800:
        relogio.agora += 300
        cookie = cipher.seal(cipher.open(cookie))
    assert cipher.open(cookie).issued_at == int(login)

    relogio.agora += 300
    with pytest.raises(InvalidCredentialsError):
        cipher.open(cookie)


def test_cookie_sem_hora_do_login_e_recusado() -> None:
    chave = base64.urlsafe_b64encode(hashlib.sha256(SEGREDO.encode()).digest())
    antigo = Fernet(chave).encrypt(
        json.dumps({"username": "operador", "token": TOKEN}).encode()
    )
    with pytest.raises(InvalidCredentialsError):
        SessionCipher(SEGREDO, 600, 28_800).open(antigo.decode())


def test_pagina_autenticada_renova_o_cookie(tmp_path: Path) -> None:
    with _cliente(tmp_path, set()) as client:
        _login(client)
        resposta = client.get("/")

    renovados = _cookies_de_sessao(resposta)
    assert len(renovados) == 1
    assert "Max-Age=600" in renovados[0]
    assert "HttpOnly" in renovados[0] and "Secure" in renovados[0]


def test_keepalive_renova_e_exige_sessao(tmp_path: Path) -> None:
    with _cliente(tmp_path, set()) as client:
        anonimo = client.get("/session/keepalive", follow_redirects=False)
        _login(client)
        autenticado = client.get("/session/keepalive")

    assert anonimo.status_code == 303
    assert anonimo.headers["location"] == "/login"
    assert autenticado.status_code == 204
    assert len(_cookies_de_sessao(autenticado)) == 1


def test_logout_nao_e_desfeito_pela_renovacao(tmp_path: Path) -> None:
    with _cliente(tmp_path, set()) as client:
        _login(client)
        resposta = client.post("/logout", follow_redirects=False)

    cookies = _cookies_de_sessao(resposta)
    assert len(cookies) == 1
    assert "Max-Age=0" in cookies[0]


def test_csp_permite_so_conexao_na_mesma_origem(tmp_path: Path) -> None:
    with _cliente(tmp_path, set()) as client:
        pagina = client.get("/login")
        script = client.get("/static/keepalive.js")

    assert "connect-src 'self'" in pagina.headers["content-security-policy"]
    assert script.status_code == 200
    assert "/session/keepalive" in script.text


def test_teto_nao_pode_ser_menor_que_a_inatividade(tmp_path: Path) -> None:
    base = _settings(tmp_path).model_dump()
    base["viewer_session_secret"] = SEGREDO
    with pytest.raises(ValidationError):
        Settings(**{**base, "viewer_session_ttl_seconds": 1800, "viewer_session_max_seconds": 900})
    with pytest.raises(ValidationError):
        Settings(**{**base, "viewer_session_max_seconds": 100_000})
