import asyncio
import os
import subprocess
from contextlib import suppress

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class ScanRequest(BaseModel):
    """Payload efêmero: nunca é gravado em disco, log ou relatório."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=10 * 1024 * 1024)


class ScanResponse(BaseModel):
    detected: bool
    # Somente o identificador da regra que casou. O motivo atravessa; o valor
    # nunca. Vazia quando o gitleaks não informou nenhuma regra reconhecível.
    rules: list[str] = []


# Um id de regra é um nome do nosso próprio TOML -- `lucien-snmp-community` --
# ou uma regra padrão do gitleaks. Nunca contém espaço nem pontuação de texto.
# Validar aqui é o que garante que, mesmo se o formato de saída do gitleaks
# mudar, nada além de um identificador saia deste serviço.
_RULE_ID = __import__("re").compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_RULES = 8


def _regras_do_achado(saida: bytes | None) -> tuple[str, ...]:
    """Extrai apenas as linhas `RuleID:` do relatório do gitleaks.

    O relatório traz também `Finding`, `Secret`, `Match` e `File`. Nenhum
    desses é lido: percorrer linha a linha e aceitar só o prefixo `RuleID:` é
    mais seguro do que confiar que `--redact=100` cobriu tudo.

    Falha fechada quanto à informação, nunca quanto ao veredito: qualquer coisa
    inesperada devolve tupla vazia, e a recusa acontece do mesmo jeito.
    """

    if not saida:
        return ()
    encontradas: list[str] = []
    for linha in saida.decode("utf-8", errors="replace").splitlines():
        limpa = linha.strip()
        if not limpa.startswith("RuleID:"):
            continue
        candidata = limpa[len("RuleID:") :].strip()
        if _RULE_ID.fullmatch(candidata):
            encontradas.append(candidata)
    return tuple(sorted(dict.fromkeys(encontradas))[:_MAX_RULES])


def _inteiro(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    """Lê um limite do ambiente sem deixar o serviço subir mal configurado.

    Um valor absurdo aqui vira processo demais ou espera eterna, e as duas
    coisas aparecem como indisponibilidade sob carga -- o pior momento para
    descobrir que a configuração estava errada.
    """

    bruto = os.environ.get(nome, "").strip()
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError as erro:
        raise RuntimeError(f"{nome} deve ser inteiro") from erro
    if not minimo <= valor <= maximo:
        raise RuntimeError(f"{nome} deve ficar entre {minimo} e {maximo}")
    return valor


GITLEAKS_TIMEOUT_SECONDS = _inteiro("SCANNER_TIMEOUT_SECONDS", 5, 1, 120)
# Teto de processos gitleaks simultâneos. Sem ele, um lote de uploads cria um
# subprocesso por requisição até onde o contêiner aguentar -- e um scanner que
# cai por exaustão bloqueia toda publicação, porque a política é fail-closed.
GITLEAKS_MAX_CONCURRENCY = _inteiro("SCANNER_MAX_CONCURRENCY", 4, 1, 64)
# Espera limitada por uma vaga. Preferimos recusar rápido a enfileirar sem
# limite: o Hub trata 503 como falha de upstream e reagenda o Job, enquanto
# uma espera indefinida seguraria conexões até estourar em outro lugar.
GITLEAKS_QUEUE_TIMEOUT_SECONDS = _inteiro("SCANNER_QUEUE_TIMEOUT_SECONDS", 10, 1, 300)
# Regras adicionais de rede de acesso e transporte, sobre o conjunto padrao.
# Ausencia do arquivo e falha de build, nao de runtime: preferimos nao subir
# um scanner mais fraco do que o esperado sem ninguem perceber.
GITLEAKS_CONFIG = "/etc/lucien/gitleaks.toml"

_vagas: asyncio.Semaphore | None = None


def _limite() -> asyncio.Semaphore:
    """Semáforo criado no laço em execução, não na importação."""

    global _vagas
    if _vagas is None:
        _vagas = asyncio.Semaphore(GITLEAKS_MAX_CONCURRENCY)
    return _vagas


async def _encerrar(processo: asyncio.subprocess.Process) -> None:
    """Mata e aguarda, sempre nessa ordem.

    Matar sem aguardar deixa zumbi, e zumbi conta para o limite de processos
    do contêiner: a saturação voltaria pela porta dos fundos.
    """

    if processo.returncode is not None:
        return
    with suppress(ProcessLookupError):
        processo.kill()
    with suppress(ProcessLookupError):
        await processo.wait()


async def _executar(content: str) -> tuple[bool, tuple[str, ...]]:
    try:
        processo = await asyncio.create_subprocess_exec(
            "gitleaks",
            "stdin",
            f"--config={GITLEAKS_CONFIG}",
            "--no-banner",
            "--no-color",
            "--redact=100",
            # Sem --verbose o gitleaks só devolve o código de saída, e a recusa
            # chega ao operador sem dizer o que casou. Com ele vem o RuleID --
            # e só o RuleID atravessa daqui, por `_regras_do_achado`.
            "--verbose",
            "--exit-code=23",
            f"--timeout={GITLEAKS_TIMEOUT_SECONDS}",
            stdin=asyncio.subprocess.PIPE,
            # A saída passa a ser lida, não descartada. `--redact=100` já
            # substitui o valor, e a extração aceita exclusivamente linhas
            # `RuleID:`; nada mais sai desta função.
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise HTTPException(
            status_code=503, detail="motor de secret scanning indisponível"
        ) from error

    try:
        saida, _ = await asyncio.wait_for(
            processo.communicate(content.encode("utf-8")),
            timeout=GITLEAKS_TIMEOUT_SECONDS + 1,
        )
    except TimeoutError as error:
        await _encerrar(processo)
        raise HTTPException(
            status_code=503, detail="secret scanning excedeu o tempo permitido"
        ) from error
    except asyncio.CancelledError:
        # Cliente desistiu. O processo precisa morrer junto, senão cada
        # desistência deixa um gitleaks vivo consumindo a cota.
        await _encerrar(processo)
        raise

    if processo.returncode == 0:
        return False, ()
    if processo.returncode == 23:
        return True, _regras_do_achado(saida)
    raise HTTPException(status_code=503, detail="secret scanner falhou de forma segura")


async def scan_content(content: str) -> tuple[bool, tuple[str, ...]]:
    """Executa Gitleaks por stdin e descarta toda saída que possa conter segredo."""

    vagas = _limite()
    try:
        await asyncio.wait_for(
            vagas.acquire(), timeout=GITLEAKS_QUEUE_TIMEOUT_SECONDS
        )
    except TimeoutError as error:
        raise HTTPException(
            status_code=503, detail="secret scanning saturado; tente novamente"
        ) from error
    try:
        return await _executar(content)
    finally:
        vagas.release()


async def _motor_responde() -> bool:
    """Confirma que o gitleaks executa, sem analisar conteúdo nenhum.

    `version` não recebe entrada e não produz achado: a prontidão é verificada
    sem que nada sensível passe pelo processo ou pelo log.
    """

    try:
        processo = await asyncio.create_subprocess_exec(
            "gitleaks",
            "version",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        await asyncio.wait_for(processo.wait(), timeout=GITLEAKS_TIMEOUT_SECONDS)
    except TimeoutError:
        await _encerrar(processo)
        return False
    return processo.returncode == 0


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    """Vivacidade: o processo responde. Não diz nada sobre o motor."""

    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Prontidão: o gitleaks pode ser executado de fato.

    Separada da vivacidade de propósito. Um serviço vivo com o binário
    quebrado aceitaria requisições e responderia 503 em todas -- e, como a
    política é fail-closed, isso pararia a publicação sem que o orquestrador
    tirasse a réplica de rotação.
    """

    if not await _motor_responde():
        raise HTTPException(
            status_code=503, detail="motor de secret scanning indisponível"
        )
    return {"status": "ready"}


@app.post("/scan", response_model=ScanResponse)
async def scan(payload: ScanRequest) -> ScanResponse:
    detectado, regras = await scan_content(payload.content)
    return ScanResponse(detected=detectado, rules=list(regras))
