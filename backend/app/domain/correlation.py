import re
from contextvars import ContextVar, Token
from uuid import uuid4

# Um cliente pode propor o próprio identificador para correlacionar os dois
# lados, mas o valor é entrada não confiável: ele vai parar em arquivo de log
# lido por humanos e por ferramenta. Aceitar só este alfabeto impede quebra de
# linha, escape de terminal e campo gigante na trilha.
_ACEITAVEL = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_ATUAL: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def novo_identificador() -> str:
    return uuid4().hex


def identificador_aceitavel(proposto: str | None) -> str:
    """Devolve o identificador do cliente, ou um novo se ele não servir."""
    if proposto is not None and _ACEITAVEL.fullmatch(proposto):
        return proposto
    return novo_identificador()


def definir_correlacao(identificador: str) -> Token[str | None]:
    """Fixa o identificador da requisição atual. Devolve o token de reversão."""
    return _ATUAL.set(identificador)


def limpar_correlacao(token: Token[str | None]) -> None:
    _ATUAL.reset(token)


def correlacao_atual() -> str | None:
    """Nulo fora de uma requisição -- o worker não atende ninguém."""
    return _ATUAL.get()
