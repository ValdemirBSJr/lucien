"""O que a trilha de auditoria registra.

O conteúdo do evento é regra de segurança: somente identificadores, papéis e
resultados de mutação. Token, log de terminal e Markdown nunca entram.

Onde esses bytes vão parar -- stdout do contêiner, formato, nível -- é decisão
de implantação e mora em `infrastructure/audit.py`. As duas metades se
encontram pelo nome do logger.
"""

import json
import logging
from datetime import UTC, datetime

from app.domain.correlation import correlacao_atual

LOGGER_AUDITORIA = "lucien.audit"

_LOGGER = logging.getLogger(LOGGER_AUDITORIA)


def audit_event(event: str, actor_id: str, **fields: str) -> None:
    payload = {
        "event": event,
        "actor_id": actor_id,
        "at": datetime.now(UTC).isoformat(),
        **fields,
    }
    # Ausente fora de uma requisição: o worker não atende ninguém, e um campo
    # nulo na trilha só faria ruído.
    correlacao = correlacao_atual()
    if correlacao is not None:
        payload["correlation_id"] = correlacao
    _LOGGER.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
