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
    """O veredito continua vindo do codigo de saida; a regra e acrescimo.

    `scan_content` passou a devolver (detected, rules) para que a recusa possa
    dizer o que casou. O primeiro elemento e o mesmo de antes.
    """

    _instala_duble(monkeypatch, demora=0.0, codigo=0)
    assert await main.scan_content("limpo") == (False, ())

    _instala_duble(monkeypatch, demora=0.0, codigo=23)
    detectado, _ = await main.scan_content("com segredo")
    assert detectado is True


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


def test_extrai_apenas_o_identificador_da_regra() -> None:
    """O relatorio do gitleaks traz Finding, Secret e Match. Nada disso sai.

    Percorrer linha a linha e aceitar so o prefixo `RuleID:` e mais seguro do
    que confiar que --redact=100 cobriu tudo: se o formato mudar, o pior caso
    e perder o motivo, nunca vazar o valor.
    """

    from app.main import _regras_do_achado

    relatorio = (
        b"Finding:     snmp-server community S3cr3tRW RW\n"  # gitleaks:allow
        b"Secret:      S3cr3tRW\n"
        b"RuleID:      lucien-snmp-community\n"
        b"Entropy:     2.750000\n"
        b"File:        /dev/stdin\n"
        b"\n"
        b"Finding:     enable secret 5 $1$abc$xyz\n"  # gitleaks:allow
        b"Secret:      $1$abc$xyz\n"
        b"RuleID:      lucien-vendor-cipher-password\n"
    )

    regras = _regras_do_achado(relatorio)

    assert regras == (
        "lucien-snmp-community",
        "lucien-vendor-cipher-password",
    )
    for proibido in (b"S3cr3tRW", b"$1$abc$xyz", b"snmp-server community"):
        assert proibido.decode() not in " ".join(regras)


def test_id_de_regra_malformado_nao_atravessa() -> None:
    """Se o formato mudar e trouxer texto, nada sai -- so o veredito.

    Um `RuleID:` com espaco ou pontuacao nao e identificador: e conteudo. A
    validacao existe para esse caso, que nenhum teste de formato atual cobre.
    """

    from app.main import _regras_do_achado

    assert _regras_do_achado(b"RuleID:      senha do cliente: abc123\n") == ()
    assert _regras_do_achado(b"RuleID:      " + b"x" * 200 + b"\n") == ()
    assert _regras_do_achado(b"Secret:      lucien-snmp-community\n") == ()
    assert _regras_do_achado(None) == ()
    assert _regras_do_achado(b"") == ()


def test_regras_repetidas_e_limitadas() -> None:
    from app.main import _regras_do_achado

    muitas = b"".join(
        f"RuleID:      regra-{i}\n".encode() for i in range(20)
    )
    assert len(_regras_do_achado(muitas)) == 8

    repetida = b"RuleID: lucien-snmp-community\n" * 5
    assert _regras_do_achado(repetida) == ("lucien-snmp-community",)


def test_diretiva_inline_do_gitleaks_nao_atravessa_o_conteudo() -> None:
    """`gitleaks:allow` num runbook desligaria a politica inteira.

    A diretiva e honrada em qualquer ponto da linha, inclusive por stdin. Sem
    desarma-la, bastaria escrever o comentario ao lado do segredo para publicar
    o segredo -- e a recusa nem apareceria, porque o gitleaks nao reporta o
    achado suprimido.
    """

    from app.main import _sem_diretiva_de_fuga

    # A diretiva de fora e para o portao do CI, que varre este arquivo. O
    # que o teste exercita e a de dentro da string, como um runbook a traria.
    linha = "enable secret 5 $1$abc$xyz  # gitleaks:allow"  # gitleaks:allow
    segredo, _, _ = linha.partition("  # ")
    desarmada = _sem_diretiva_de_fuga(linha)

    assert "gitleaks:allow" not in desarmada
    assert segredo in desarmada
    # Mesmo comprimento: o relatorio aponta coluna, e deslocar mentiria.
    assert len(desarmada) == len(linha)


def test_desarme_nao_toca_em_texto_que_nao_e_a_diretiva() -> None:
    from app.main import _sem_diretiva_de_fuga

    for intacto in (
        "gitleaks:ignore",
        "GITLEAKS:ALLOW",
        "gitleaks : allow",
        "o portao gitleaks aprovou",
        "",
    ):
        assert _sem_diretiva_de_fuga(intacto) == intacto
