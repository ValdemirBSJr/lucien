import httpx

from app.domain.ports import SecretScanner, UpstreamError


class GitleaksSecretScanner(SecretScanner):
    """Adapter HTTP para o scanner isolado, sem registrar o conteúdo analisado."""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        # Cliente compartilhado: um upload dispara múltiplas barreiras de scan e
        # não deve pagar o setup de conexão em cada uma delas.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def detect(self, content: str) -> bool:
        try:
            response = await self._client.post("/scan", json={"content": content})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, TypeError, ValueError) as error:
            raise UpstreamError("secret scanner indisponível; conteúdo não foi aceito") from error

        detected = payload.get("detected")
        if type(detected) is not bool:
            raise UpstreamError("secret scanner retornou resposta inválida")
        return detected

    async def aclose(self) -> None:
        await self._client.aclose()
