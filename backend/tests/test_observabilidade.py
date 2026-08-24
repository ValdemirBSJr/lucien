"""Correlação, prontidão e contadores operacionais.

Quando alguém relata "meu job falhou", a única pista costumava ser o horário
aproximado, e `/health` respondia `ok` mesmo com o banco fora. Estes testes
cobrem o que passou a existir para responder duas perguntas: *qual requisição
foi essa* e *o Hub consegue atender agora*.
"""

import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.domain.audit import audit_event
from app.domain.correlation import (
    correlacao_atual,
    definir_correlacao,
    identificador_aceitavel,
    limpar_correlacao,
)


@pytest.fixture
def cliente(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "b" * 32)
    monkeypatch.setenv("AUTH_PEPPER", "p" * 32)
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'obs.db').as_posix()}",
        bootstrap_api_key="b" * 32,
        auth_pepper="p" * 32,
        slm_language_runbook="en",
        user_creation_enabled=True,
        allow_insecure_dev=True,
        local_storage_root=tmp_path / "playbooks",
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        yield app, client


def _admin(client: TestClient) -> str:
    resposta = client.post(
        "/bootstrap/admin",
        headers={"Authorization": f"Bearer {'b' * 32}"},
        json={"username": "root-admin", "domain_function": "plataforma"},
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["api_token"]


class _Captura(logging.Handler):
    """Ouve a trilha diretamente.

    `configure_audit_logging` desliga a propagação para o logger raiz, então o
    caplog do pytest não enxerga estes registros -- e essa configuração é
    justamente a que roda em produção.
    """

    def __init__(self) -> None:
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensagens.append(record.getMessage())


@pytest.fixture
def trilha():
    logger = logging.getLogger("lucien.audit")
    handler = _Captura()
    nivel = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler.mensagens
    finally:
        logger.removeHandler(handler)
        logger.setLevel(nivel)


# --- correlação --------------------------------------------------------------


def test_identificador_do_cliente_e_aceito_quando_e_seguro() -> None:
    assert identificador_aceitavel("cli-2026-08-21-abcdef") == "cli-2026-08-21-abcdef"


@pytest.mark.parametrize(
    "proposto",
    [
        None,
        "curto",
        "x" * 65,
        "com espaço",
        "quebra\nde-linha",
        "\x1b[31mescape-de-terminal",
        "ponto;e;virgula",
    ],
)
def test_identificador_hostil_e_descartado(proposto: str | None) -> None:
    """O valor vai parar em log lido por humanos e por ferramenta.

    Aceitar quebra de linha deixaria um cliente forjar uma entrada inteira na
    trilha de auditoria; aceitar escape de terminal deixaria mexer no terminal
    de quem lesse o log.
    """
    resultado = identificador_aceitavel(proposto)
    assert resultado != proposto
    assert resultado.isalnum()
    assert len(resultado) == 32


def test_resposta_devolve_o_identificador(cliente) -> None:
    _, client = cliente
    resposta = client.get("/health")
    assert resposta.headers["X-Request-Id"]


def test_identificador_proposto_atravessa_a_requisicao(cliente) -> None:
    _, client = cliente
    proposto = "chamado-4711-abcdef"
    resposta = client.get("/health", headers={"X-Request-Id": proposto})
    assert resposta.headers["X-Request-Id"] == proposto


def test_erro_carrega_o_identificador_no_corpo(cliente) -> None:
    """Quem relata um erro copia o que está na tela, não o cabeçalho."""
    _, client = cliente
    token = _admin(client)
    resposta = client.get(
        "/jobs/00000000-0000-4000-8000-000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resposta.status_code == 404
    corpo = resposta.json()
    assert corpo["request_id"] == resposta.headers["X-Request-Id"]


def test_requisicao_recusada_na_borda_tambem_tem_identificador(cliente) -> None:
    """O caso mais difícil de investigar não pode ser o que fica sem rastro."""
    _, client = cliente
    resposta = client.get("/jobs", headers={"Authorization": "Bearer invalido"})
    assert resposta.status_code == 401
    assert resposta.headers["X-Request-Id"]


def test_trilha_de_auditoria_carrega_a_correlacao(trilha) -> None:
    token = definir_correlacao("correlacao-de-teste-01")
    try:
        audit_event("user.revoke", actor_id="ator", target_id="alvo")
    finally:
        limpar_correlacao(token)

    assert json.loads(trilha[-1])["correlation_id"] == "correlacao-de-teste-01"


def test_fora_de_requisicao_a_trilha_nao_inventa_correlacao(trilha) -> None:
    """O worker não atende ninguém; um campo nulo ali só faria ruído."""
    assert correlacao_atual() is None
    audit_event("upload.processed", actor_id="worker")

    assert "correlation_id" not in json.loads(trilha[-1])


# --- prontidão ---------------------------------------------------------------


def test_health_nao_consulta_o_banco(cliente, monkeypatch) -> None:
    """Reiniciar o Hub não conserta banco fora, e é isso que healthcheck
    reprovado provoca."""
    app, client = cliente

    async def explodir() -> None:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(app.state.repository, "ping", explodir)
    assert client.get("/health").status_code == 200


def test_ready_reprova_quando_o_banco_nao_responde(cliente, monkeypatch) -> None:
    app, client = cliente
    assert client.get("/ready").json()["status"] == "pronto"

    async def explodir() -> None:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(app.state.repository, "ping", explodir)
    resposta = client.get("/ready")
    assert resposta.status_code == 503
    assert resposta.json()["database"] == "inalcançável"


def test_ready_dispensa_credencial(cliente) -> None:
    """Uma sonda não carrega token."""
    _, client = cliente
    assert client.get("/ready").status_code == 200


# --- métricas ----------------------------------------------------------------


def test_metrics_exige_admin(cliente) -> None:
    _, client = cliente
    assert client.get("/metrics").status_code == 401

    token = _admin(client)
    assert client.get(
        "/metrics", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200


def test_metrics_relata_fila_e_jobs(cliente) -> None:
    _, client = cliente
    token = _admin(client)
    corpo = client.get(
        "/metrics", headers={"Authorization": f"Bearer {token}"}
    ).text

    nomes = {linha.split(" ")[0] for linha in corpo.splitlines()}
    assert "lucien_upload_queue_profundidade" in nomes
    # Idade do mais antigo, não só a contagem: cinco itens que chegaram agora e
    # cinco presos há quarenta minutos contam igual e significam coisas opostas.
    assert "lucien_upload_queue_idade_maxima_segundos" in nomes
    for estado in ("pending", "processing", "published", "failed"):
        assert f"lucien_jobs_{estado}" in nomes
