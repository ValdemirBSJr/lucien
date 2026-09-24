from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.infrastructure.security import RequestSizeMiddleware

MIB = 1024 * 1024
JOB_ID = "11111111-1111-4111-8111-111111111111"


def _app() -> TestClient:
    """Rotas mínimas atrás do filtro, com os limites padrão do Hub."""

    app = FastAPI()

    @app.post("/jobs/{id_or_name}/publish")
    @app.post("/runbooks/{job_id}/revisions")
    @app.post("/upload")
    async def aceita(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    app.add_middleware(
        RequestSizeMiddleware,
        max_body_bytes=2 * MIB + 128 * 1024,
        max_publication_bytes=16 * MIB,
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "rota", [f"/jobs/{JOB_ID}/publish", "/jobs/nome-do-job/publish", f"/runbooks/{JOB_ID}/revisions"]
)
def test_publicacao_com_imagens_passa_do_limite_do_log(rota: str) -> None:
    # O caso de produção: oito capturas de tela em base64 passavam de 2 MiB, e
    # o Hub recusava a publicação antes de olhar as imagens.
    resposta = _app().post(rota, content=b"x" * (3 * MIB))

    assert resposta.status_code == 200
    assert resposta.json() == {"bytes": 3 * MIB}


@pytest.mark.parametrize("rota", [f"/jobs/{JOB_ID}/publish", f"/runbooks/{JOB_ID}/revisions"])
def test_publicacao_acima_do_teto_diz_o_motivo_em_ingles(rota: str) -> None:
    resposta = _app().post(rota, content=b"x" * (17 * MIB))

    assert resposta.status_code == 413
    assert resposta.json()["detail"] == (
        "request body is 17.0 MiB, above the 16.0 MiB limit for publishing a "
        "runbook with its images; review MAX_PUBLICATION_BYTES on the Hub"
    )


def test_demais_rotas_seguem_o_limite_do_log() -> None:
    # O teto novo vale só para as rotas que levam imagens: o upload de log
    # continua recusado no mesmo tamanho de antes.
    resposta = _app().post("/upload", content=b"x" * (3 * MIB))

    assert resposta.status_code == 413
    assert resposta.json()["detail"] == (
        "request body is 3.0 MiB, above the 2.1 MiB limit for this request; "
        "review MAX_LOG_BYTES on the Hub"
    )


def test_rota_parecida_nao_ganha_o_teto_de_publicacao() -> None:
    # A comparação é do caminho inteiro: um sufixo ou prefixo a mais não pode
    # herdar os 16 MiB.
    for rota in (f"/jobs/{JOB_ID}/publish/extra", f"/x/jobs/{JOB_ID}/publish"):
        resposta = _app().post(rota, content=b"x" * (3 * MIB))
        assert resposta.status_code == 413, rota
        assert "MAX_LOG_BYTES" in resposta.json()["detail"], rota


def test_corpo_sem_tamanho_conhecido_continua_recusado() -> None:
    cliente = _app()

    sem_tamanho = cliente.post(
        f"/jobs/{JOB_ID}/publish", content=iter([b"x"]), headers={"Content-Type": "text/plain"}
    )
    invalido = cliente.post(f"/jobs/{JOB_ID}/publish", content=b"x", headers={"Content-Length": "abc"})

    assert sem_tamanho.status_code == 411
    assert invalido.status_code == 400


def test_max_publication_bytes_tem_piso_e_teto() -> None:
    base = {"database_url": "sqlite+aiosqlite:///:memory:", "bootstrap_api_key": "b" * 32, "auth_pepper": "p" * 32}

    assert Settings(**base).max_publication_bytes == 16 * MIB
    with pytest.raises(ValidationError):
        Settings(**base, max_publication_bytes=MIB)
    with pytest.raises(ValidationError):
        Settings(**base, max_publication_bytes=65 * MIB)


def test_app_do_hub_liga_os_dois_limites(tmp_path: Path, monkeypatch) -> None:
    # A ligação real, no create_app: sem ela os testes acima provariam só o
    # middleware isolado.
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "b" * 32)
    monkeypatch.setenv("AUTH_PEPPER", "p" * 32)
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tamanho.db').as_posix()}",
        bootstrap_api_key="b" * 32,
        auth_pepper="p" * 32,
        allow_insecure_dev=True,
        local_storage_root=tmp_path / "playbooks",
    )
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        publicacao = client.post(
            f"/jobs/{JOB_ID}/publish",
            content=b"x" * (3 * MIB),
            headers={"Content-Type": "application/json", "Idempotency-Key": "k" * 16},
        )
        upload = client.post(
            "/upload", content=b"x" * (3 * MIB), headers={"Content-Type": "application/json"}
        )

    # Passou do filtro de tamanho e parou na autenticação, que vem depois.
    assert publicacao.status_code == 401
    assert upload.status_code == 413
    assert "MAX_LOG_BYTES" in upload.json()["detail"]
