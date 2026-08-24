import re
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import SESSION_COOKIE, create_app
from app.models import AuthenticatedUser
from app.repository import RunbookRepository
from app.security import (
    IdentityUnavailableError,
    InvalidCredentialsError,
    RevisionPreconditionFailedError,
)


class FakeIdentityVerifier:
    def __init__(
        self,
        role: str = "senior",
        domain: str = "servidores",
        published_ids: set[str] | None = None,
    ) -> None:
        self.active = True
        self.calls = 0
        self.role = role
        self.domain = domain
        self.published_ids = frozenset(published_ids or set())
        self.revision_calls: list[dict[str, str]] = []
        self.revision_error: Exception | None = None

    async def verify(self, username: str, token: str) -> AuthenticatedUser:
        self.calls += 1
        if not self.active or username != "operador" or token != "luc_token_valido_123456":
            raise InvalidCredentialsError
        return AuthenticatedUser("user-1", "operador", self.role, self.domain)

    async def list_published_ids(self, token: str) -> frozenset[str]:
        if token != "luc_token_valido_123456":
            raise InvalidCredentialsError
        return self.published_ids

    async def create_revision(
        self,
        current_job_id: str,
        markdown: str,
        body_hash: str,
        idempotency_key: str,
        token: str,
    ) -> None:
        self.revision_calls.append(
            {
                "current_job_id": current_job_id,
                "markdown": markdown,
                "body_hash": body_hash,
                "idempotency_key": idempotency_key,
                "token": token,
            }
        )
        if self.revision_error is not None:
            raise self.revision_error


def _settings(root: Path) -> Settings:
    return Settings(
        viewer_hub_url="https://hub:8443",
        viewer_hub_ca_file=root / "ca.crt",
        viewer_session_secret=SecretStr("segredo-de-sessao-com-mais-de-32-bytes-aleatorios"),
        viewer_runbooks_root=root,
        viewer_session_ttl_seconds=900,
        viewer_max_documents=100,
        viewer_max_file_bytes=1024 * 1024,
    )


def _write_runbook(root: Path, runbook_id: str) -> None:
    target = root / "2026" / "07" / f"{runbook_id}.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        f'id: "{runbook_id}"\n'
        'autor: "operador"\n'
        'nivel_autor: "senior"\n'
        'funcao: "servidores"\n'
        'data_criacao: "2026-07-22T18:00:00Z"\n'
        'tags_inferidas: ["linux", "systemd"]\n'
        "---\n"
        "# Reiniciar API\n\n### Passo 1: Reiniciar\n```bash\nsystemctl restart api\n```\n",
        encoding="utf-8",
    )


def _csrf(client: TestClient) -> str:
    response = client.get("/login")
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={
            "username": "operador",
            "api_token": "luc_token_valido_123456",
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_login_cookie_seguro_catalogo_e_revalidacao(tmp_path: Path) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    verifier = FakeIdentityVerifier(published_ids={runbook_id})
    repository = RunbookRepository(tmp_path, 100, 1024 * 1024, 60)
    app = create_app(_settings(tmp_path), verifier, repository)

    with TestClient(app, base_url="https://viewer.test") as client:
        csrf = _csrf(client)
        response = client.post(
            "/login",
            data={
                "username": "operador",
                "api_token": "luc_token_valido_123456",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        cookie_header = response.headers["set-cookie"]
        assert response.status_code == 303
        assert SESSION_COOKIE in cookie_header
        assert "luc_token_valido_123456" not in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Secure" in cookie_header
        assert "SameSite=strict" in cookie_header

        catalog = client.get("/")
        detail = client.get(f"/runbooks/{runbook_id}")
        assert catalog.status_code == 200
        assert "Reiniciar API" in catalog.text
        assert detail.status_code == 200
        assert "systemctl restart api" in detail.text
        assert verifier.calls == 3  # login e cada página protegida
        assert "default-src &#39;self&#39;" not in catalog.text
        assert catalog.headers["content-security-policy"].startswith("default-src")
        assert catalog.headers["strict-transport-security"].startswith("max-age=")
        assert catalog.headers["cache-control"] == "private, no-store, max-age=0"


def test_token_revogado_invalida_proxima_pagina(tmp_path: Path) -> None:
    verifier = FakeIdentityVerifier()
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        verifier.active = False
        response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert f"{SESSION_COOKIE}=\"\"" in response.headers["set-cookie"] or f"{SESSION_COOKIE}=" in response.headers["set-cookie"]


def test_login_exige_csrf_e_nao_aceita_username_divergente(tmp_path: Path) -> None:
    verifier = FakeIdentityVerifier()
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        missing_csrf = client.post(
            "/login",
            data={
                "username": "operador",
                "api_token": "luc_token_valido_123456",
                "csrf_token": "invalido",
            },
        )
        divergent = client.post(
            "/login",
            data={
                "username": "outro",
                "api_token": "luc_token_valido_123456",
                "csrf_token": _csrf(client),
            },
        )

    assert missing_csrf.status_code == 400
    assert divergent.status_code == 401
    assert "Usuário ou token inválido" in divergent.text


def test_login_limita_payload_antes_de_processar_formulario(tmp_path: Path) -> None:
    verifier = FakeIdentityVerifier()
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        response = client.post(
            "/login",
            content=b"username=" + (b"a" * (17 * 1024)),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    assert verifier.calls == 0


def test_rotas_nao_oferecem_mutacao_de_runbook(tmp_path: Path) -> None:
    verifier = FakeIdentityVerifier()
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        assert client.post(f"/runbooks/{uuid4()}").status_code == 405
        assert client.put(f"/runbooks/{uuid4()}").status_code == 405
        assert client.delete(f"/runbooks/{uuid4()}").status_code == 405
        assert client.get("/runbooks/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_health_e_publico_e_tema_nao_depende_de_cdn(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path),
        FakeIdentityVerifier(),
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        health = client.get("/health")
        login = client.get("/login")
        theme = client.get("/static/theme.js")

    assert health.json() == {"status": "ok"}
    assert login.status_code == 200
    assert "prefers-color-scheme" not in login.text
    assert "cdn.jsdelivr" not in login.text
    assert "unpkg.com" not in login.text
    assert "localStorage" in theme.text


def _edit_state(response_text: str) -> str:
    match = re.search(r'name="edit_state" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def test_senior_do_mesmo_dominio_cria_revisao_sem_escrever_no_volume(
    tmp_path: Path,
) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    original = (tmp_path / "2026" / "07" / f"{runbook_id}.md").read_bytes()
    verifier = FakeIdentityVerifier(
        role="senior", domain="servidores", published_ids={runbook_id}
    )
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )

    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        detail = client.get(f"/runbooks/{runbook_id}")
        assert f'/runbooks/{runbook_id}/edit' in detail.text
        edit = client.get(f"/runbooks/{runbook_id}/edit")
        state = _edit_state(edit.text)
        revised = "# API revisada\n\n### Passo 1: Validar\n```bash\nid\n```\n"
        submitted = client.post(
            f"/runbooks/{runbook_id}/edit",
            data={"markdown": revised, "edit_state": state},
            follow_redirects=False,
        )

    assert submitted.status_code == 303
    assert submitted.headers["location"] == f"/runbooks/{runbook_id}"
    assert len(verifier.revision_calls) == 1
    call = verifier.revision_calls[0]
    assert call["current_job_id"] == runbook_id
    assert call["markdown"] == revised
    assert len(call["body_hash"]) == 64
    assert call["idempotency_key"].startswith("revision-")
    assert call["token"] == "luc_token_valido_123456"
    assert (tmp_path / "2026" / "07" / f"{runbook_id}.md").read_bytes() == original


def test_retry_da_mesma_tela_preserva_idempotency_key_e_escapa_markdown(
    tmp_path: Path,
) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    verifier = FakeIdentityVerifier(published_ids={runbook_id})
    verifier.revision_error = IdentityUnavailableError()
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    malicious = "# Revisão\n\n</textarea><script>alert(1)</script>\n"

    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        state = _edit_state(client.get(f"/runbooks/{runbook_id}/edit").text)
        first = client.post(
            f"/runbooks/{runbook_id}/edit",
            data={"markdown": malicious, "edit_state": state},
        )
        second = client.post(
            f"/runbooks/{runbook_id}/edit",
            data={"markdown": malicious, "edit_state": state},
        )

    assert first.status_code == 503
    assert second.status_code == 503
    assert "<script>alert(1)</script>" not in first.text
    assert "&lt;/textarea&gt;&lt;script&gt;" in first.text
    assert len(verifier.revision_calls) == 2
    assert (
        verifier.revision_calls[0]["idempotency_key"]
        == verifier.revision_calls[1]["idempotency_key"]
    )


def test_edicao_na_ui_respeita_role_e_dominio_sem_substituir_rbac_do_hub(
    tmp_path: Path,
) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    for verifier in (
        FakeIdentityVerifier(
            role="junior", domain="servidores", published_ids={runbook_id}
        ),
        FakeIdentityVerifier(
            role="senior", domain="redes", published_ids={runbook_id}
        ),
    ):
        app = create_app(
            _settings(tmp_path),
            verifier,
            RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
        )
        with TestClient(app, base_url="https://viewer.test") as client:
            _login(client)
            detail = client.get(f"/runbooks/{runbook_id}")
            edit = client.get(f"/runbooks/{runbook_id}/edit")
        assert ">Editar<" not in detail.text
        assert edit.status_code == 403

    admin = FakeIdentityVerifier(
        role="admin", domain="redes", published_ids={runbook_id}
    )
    app = create_app(
        _settings(tmp_path),
        admin,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        assert client.get(f"/runbooks/{runbook_id}/edit").status_code == 200


def test_edicao_exige_estado_csrf_autenticado(tmp_path: Path) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    verifier = FakeIdentityVerifier(published_ids={runbook_id})
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )
    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        response = client.post(
            f"/runbooks/{runbook_id}/edit",
            data={"markdown": "# Alterado\n", "edit_state": "adulterado"},
        )

    assert response.status_code == 400
    assert verifier.revision_calls == []


def test_edicao_preserva_status_412_quando_base_mudou(tmp_path: Path) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    verifier = FakeIdentityVerifier(published_ids={runbook_id})
    verifier.revision_error = RevisionPreconditionFailedError()
    app = create_app(
        _settings(tmp_path),
        verifier,
        RunbookRepository(tmp_path, 100, 1024 * 1024, 60),
    )

    with TestClient(app, base_url="https://viewer.test") as client:
        _login(client)
        state = _edit_state(client.get(f"/runbooks/{runbook_id}/edit").text)
        response = client.post(
            f"/runbooks/{runbook_id}/edit",
            data={"markdown": "# Revisado\n", "edit_state": state},
        )

    assert response.status_code == 412
    assert "mudou desde a abertura" in response.text


def test_can_edit_respeita_rbac_entry_roles_enabled() -> None:
    from app.main import _can_edit
    from app.models import AuthenticatedUser

    junior = AuthenticatedUser(
        id="1", username="junior", role_level="junior", domain_function="redes"
    )
    pleno = AuthenticatedUser(
        id="2", username="pleno", role_level="pleno", domain_function="redes"
    )
    admin = AuthenticatedUser(
        id="3", username="admin", role_level="admin", domain_function="plataforma"
    )

    # Default: junior não vê o botão de edição.
    assert _can_edit(junior, "redes") is False
    assert _can_edit(junior, "redes", False) is False

    # Habilitado: junior edita, mas só o próprio domínio.
    assert _can_edit(junior, "redes", True) is True
    assert _can_edit(junior, "servidores", True) is False

    # Pleno acompanha o junior, preservando a hierarquia entre os dois papéis.
    assert _can_edit(pleno, "redes") is False
    assert _can_edit(pleno, "redes", True) is True
    assert _can_edit(pleno, "servidores", True) is False

    # Admin nunca depende da flag e não tem restrição de domínio.
    assert _can_edit(admin, "qualquer", False) is True
