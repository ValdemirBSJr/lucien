"""Para onde vai a trilha de auditoria.

O conteúdo do evento é do domínio (`app.domain.audit`). Aqui fica só a decisão
de implantação: sair no stdout do contêiner, em INFO, sem duplicar handler
quando o módulo é importado mais de uma vez.
"""

import logging
import sys

from app.domain.audit import LOGGER_AUDITORIA


def configure_audit_logging() -> None:
    """Garante saída INFO no stdout do contêiner sem duplicar handlers."""

    logger = logging.getLogger(LOGGER_AUDITORIA)
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
