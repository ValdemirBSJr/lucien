"""O scanner precisa recusar carga, não afundar com ela.

A política do Hub é fail-closed: scanner indisponível bloqueia publicação.
Isso torna a saturação especialmente cara -- um lote de uploads que derruba o
scanner para toda a instalação, não só para si.

Os testes substituem o `gitleaks` por um processo controlado. Rodar o binário
real aqui mediria a máquina, não o limite.
"""

import asyncio
import sys

import pytest
from fastapi import HTTPException

from app import main


@pytest.fixture(autouse=True)
def _semaforo_limpo():
    """Cada teste começa com o semáforo por criar, no laço do próprio teste."""

    main._vagas = None
    yield
    main._vagas = None


class _Processo:
    """Dublê de subprocesso que conta início, fim e morte."""

    def __init__(self, registro: dict, demora: float, codigo: int) -> None:
        self._registro = registro
        self._demora = demora
        self.returncode: int | None = None
        self.morto = False

    async def communicate(self, _entrada: bytes) -> tuple[bytes, bytes]:
        self._registro["vivos"] += 1
        self._registro["pico"] = max(
            self._registro["pico"], self._registro["vivos"]
        )
        try:
            await asyncio.sleep(self._demora)
        finally:
            self._registro["vivos"] -= 1
        self.returncode = self._registro["codigo"]
        return b"", b""

    def kill(self) -> None:
        self.morto = True
        self._registro["mortos"] += 1

    async def wait(self) -> int:
        self._registro["aguardados"] += 1
        self.returncode = self.returncode if self.returncode is not None else -9
        return self.returncode


def _instala_duble(monkeypatch, demora: float = 0.05, codigo: int = 0) -> dict:
    registro = {
        "vivos": 0,
        "pico": 0,
        "mortos": 0,
        "aguardados": 0,
        "criados": 0,
        "codigo": codigo,
    }

    async def _cria(*_args, **_kwargs):
        registro["criados"] += 1
        return _Processo(registro, demora, codigo)

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _cria)
    return registro


async def test_concorrencia_acima_do_limite_nao_cria_processos_extras(
    monkeypatch,
) -> None:
    """O semáforo é o que impede um lote de uploads de virar um lote de gitleaks."""

    monkeypatch.setattr(main, "GITLEAKS_MAX_CONCURRENCY", 3)
    registro = _instala_duble(monkeypatch, demora=0.05)

    await asyncio.gather(*(main.scan_content("conteudo") for _ in range(12)))

    assert registro["criados"] == 12
    # Todos executaram, mas nunca mais de três ao mesmo tempo.
    assert registro["pico"] <= 3


async def test_fila_saturada_recusa_em_vez_de_esperar_para_sempre(
    monkeypatch,
) -> None:
    """503 rápido é melhor que conexão pendurada: o Hub reagenda o Job."""

    monkeypatch.setattr(main, "GITLEAKS_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(main, "GITLEAKS_QUEUE_TIMEOUT_SECONDS", 0.05)
    _instala_duble(monkeypatch, demora=0.5)

    ocupado = asyncio.create_task(main.scan_content("primeiro"))
    await asyncio.sleep(0.01)

    with pytest.raises(HTTPException) as erro:
        await main.scan_content("segundo")

    assert erro.value.status_code == 503
    assert "saturado" in erro.value.detail
    await ocupado


async def test_vaga_e_devolvida_mesmo_quando_o_scan_falha(monkeypatch) -> None:
    """Sem o `finally`, uma falha vazaria a vaga e o limite iria a zero."""

    monkeypatch.setattr(main, "GITLEAKS_MAX_CONCURRENCY", 1)
    _instala_duble(monkeypatch, demora=0.0, codigo=99)

    for _ in range(3):
        with pytest.raises(HTTPException):
            await main.scan_content("conteudo")

    # Se a vaga tivesse vazado, a terceira chamada teria travado até o timeout.
    assert main._limite()._value == 1


async def test_timeout_mata_e_aguarda_o_processo(monkeypatch) -> None:
    """Matar sem aguardar deixa zumbi, e zumbi conta para o pids_limit."""

    monkeypatch.setattr(main, "GITLEAKS_TIMEOUT_SECONDS", 0)
    registro = _instala_duble(monkeypatch, demora=5.0)

    with pytest.raises(HTTPException) as erro:
        await main.scan_content("conteudo")

    assert erro.value.status_code == 503
    assert registro["mortos"] == 1
    assert registro["aguardados"] == 1


async def test_cancelamento_do_cliente_tambem_encerra_o_processo(
    monkeypatch,
) -> None:
    """Cada desistência deixaria um gitleaks vivo consumindo a cota."""

    registro = _instala_duble(monkeypatch, demora=5.0)

    tarefa = asyncio.create_task(main.scan_content("conteudo"))
    await asyncio.sleep(0.01)
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa

    assert registro["mortos"] == 1
    assert registro["aguardados"] == 1
    # E a vaga voltou para o próximo.
    assert main._limite()._value == main.GITLEAKS_MAX_CONCURRENCY


async def test_deteccao_e_ausencia_seguem_o_codigo_de_saida(monkeypatch) -> None:
    _instala_duble(monkeypatch, demora=0.0, codigo=0)
    assert await main.scan_content("limpo") is False

    _instala_duble(monkeypatch, demora=0.0, codigo=23)
    assert await main.scan_content("com segredo") is True


async def test_codigo_desconhecido_falha_fechado(monkeypatch) -> None:
    """Qualquer coisa fora de 0 e 23 bloqueia; liberar seria o erro grave."""

    _instala_duble(monkeypatch, demora=0.0, codigo=2)

    with pytest.raises(HTTPException) as erro:
        await main.scan_content("conteudo")

    assert erro.value.status_code == 503


async def test_prontidao_nao_analisa_conteudo(monkeypatch) -> None:
    """A verificação roda `version`: nada sensível passa pelo processo."""

    argumentos: list[tuple] = []

    class _Version:
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def _cria(*args, **_kwargs):
        argumentos.append(args)
        return _Version()

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _cria)

    assert await main._motor_responde() is True
    assert argumentos == [("gitleaks", "version")]


def test_limite_invalido_impede_o_servico_de_subir(monkeypatch) -> None:
    """Configuração absurda aparece como indisponibilidade sob carga."""

    monkeypatch.setenv("SCANNER_MAX_CONCURRENCY", "0")
    with pytest.raises(RuntimeError, match="entre 1 e 64"):
        main._inteiro("SCANNER_MAX_CONCURRENCY", 4, 1, 64)

    monkeypatch.setenv("SCANNER_MAX_CONCURRENCY", "muitos")
    with pytest.raises(RuntimeError, match="inteiro"):
        main._inteiro("SCANNER_MAX_CONCURRENCY", 4, 1, 64)

    monkeypatch.delenv("SCANNER_MAX_CONCURRENCY")
    assert main._inteiro("SCANNER_MAX_CONCURRENCY", 4, 1, 64) == 4


assert sys.version_info >= (3, 11), "TimeoutError unificado exige 3.11+"
