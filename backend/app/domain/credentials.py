"""Como uma credencial vira registro no banco.

Política, não mecanismo: o Hub nunca guarda o token, só o HMAC dele com o
pepper. Isso é uma regra de negócio sobre credencial -- por isso mora aqui, e
não junto do middleware que a aplica numa requisição HTTP.
"""

import hashlib
import hmac


def digest_api_token(api_token: str, pepper: str) -> str:
    """HMAC impede ataque offline simples caso apenas o banco seja vazado."""
    return hmac.new(pepper.encode(), api_token.encode(), hashlib.sha256).hexdigest()
