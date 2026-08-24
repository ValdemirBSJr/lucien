import asyncio

from app.application import UploadProcessor
from app.config import Settings
from app.domain.ports import RunbookEnricher
from app.infrastructure.audit import configure_audit_logging
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.enrichment import DeterministicRunbookEnricher
from app.infrastructure.secret_scanner import GitleaksSecretScanner
from app.infrastructure.slm import OllamaCommandExtractor, OllamaRunbookEnricher
from app.infrastructure.upload_cipher import AESGCMUploadCipher


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    configure_audit_logging()
    repository = SQLAlchemyJobRepository(settings.database_url)
    extractor = OllamaCommandExtractor(
        settings.slm_base_url,
        settings.slm_model,
        settings.slm_timeout_seconds,
        settings.slm_num_thread,
        settings.slm_num_ctx,
        settings.slm_prompt_max_chars,
    )
    tag_inferrer: RunbookEnricher
    if settings.runbook_enricher == "deterministic":
        tag_inferrer = DeterministicRunbookEnricher(settings.slm_language_runbook)
    else:
        tag_inferrer = OllamaRunbookEnricher(
            settings.slm_base_url,
            settings.slm_model,
            settings.slm_timeout_seconds,
            settings.slm_language_runbook,
            settings.slm_num_thread,
            settings.slm_num_ctx,
        )
    scanner = GitleaksSecretScanner(
        settings.secret_scanner_url, settings.secret_scanner_timeout_seconds
    )
    processor = UploadProcessor(
        repository=repository,
        cipher=AESGCMUploadCipher(settings.auth_pepper.get_secret_value()),
        extractor=extractor,
        tag_inferrer=tag_inferrer,
        secret_scanner=scanner,
        lease_seconds=settings.upload_worker_lease_seconds,
        retry_base_seconds=settings.upload_worker_retry_base_seconds,
        max_attempts=settings.upload_worker_max_attempts,
        enrichment_enabled=settings.slm_enrichment_enabled,
    )

    await repository.initialize()
    try:
        while True:
            processed = await processor.process_once()
            if not processed:
                await asyncio.sleep(settings.upload_worker_poll_seconds)
    finally:
        await extractor.aclose()
        await tag_inferrer.aclose()
        await scanner.aclose()
        await repository.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
