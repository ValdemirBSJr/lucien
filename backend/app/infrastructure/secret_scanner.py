import logging
import re

import httpx

from app.domain.ports import SecretScanner, SecretScanResult, UpstreamError

# O identificador de regra e o unico campo do achado que atravessa a fronteira.
# Validar o formato aqui impede que uma mudanca no formato de saida do gitleaks
# -- ou um campo inesperado -- traga trecho de conteudo junto.
_RULE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_RULES = 8

_log = logging.getLogger(__name__)


class GitleaksSecretScanner(SecretScanner):
    """Adapter HTTP para o scanner isolado, sem registrar o conteúdo analisado."""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        # Cliente compartilhado: um upload dispara múltiplas barreiras de scan e
        # não deve pagar o setup de conexão em cada uma delas.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def detect(self, content: str) -> SecretScanResult:
        try:
            response = await self._client.post("/scan", json={"content": content})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, TypeError, ValueError) as error:
            # O conteudo escaneado nunca aparece aqui -- so o tipo e a mensagem
            # da excecao do httpx (timeout, conexao recusada, etc.), o
            # suficiente para distinguir "scanner fora do ar" de "scanner lento
            # sob carga" na proxima ocorrencia, sem virar caixa-preta de novo.
            _log.warning(
                "chamada ao secret scanner falhou tipo=%s detalhe=%s",
                type(error).__name__,
                error,
            )
            raise UpstreamError("secret scanner unavailable; the content was not accepted") from error

        detected = payload.get("detected")
        if type(detected) is not bool:
            raise UpstreamError("secret scanner returned an invalid response")
        return SecretScanResult(detected=detected, rules=_regras(payload.get("rules")))

    async def aclose(self) -> None:
        await self._client.aclose()


def _regras(bruto: object) -> tuple[str, ...]:
    """Aceita a lista de regras sem deixar passar nada que não seja um id.

    Ausente ou malformada não é erro: um scanner anterior a esta mudança não
    informa regra, e a recusa continua correta sem ela. Derrubar a publicação
    por causa do campo informativo seria trocar um problema de diagnóstico por
    um de disponibilidade.
    """

    if not isinstance(bruto, list):
        return ()
    validas = [
        item
        for item in bruto
        if isinstance(item, str) and _RULE_ID.fullmatch(item)
    ]
    return tuple(sorted(dict.fromkeys(validas))[:_MAX_RULES])
