import base64
import dataclasses
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from app.domain.models import (
    DEFAULT_DOMAIN_FUNCTIONS,
    Job,
    PublicationIdentity,
    RevisionSource,
    RoleLevel,
    RunbookEnrichment,
    RunbookSuggestions,
    SecurityContext,
    User,
)
from app.domain.images import (
    extract_asset_references,
    previously_published_asset_paths,
    rewritten_markdown,
    validate_asset_completeness,
)
from app.domain.ports import (
    AssetToPublish,
    CommandExtractor,
    ConflictError,
    ForbiddenError,
    IdentityRepository,
    ImageSecurityScanner,
    JobRepository,
    NotFoundError,
    ProcessedAsset,
    RawAssetInput,
    RunbookEnricher,
    SecretDetectedError,
    SecretScanner,
    StorageProvider,
    UploadCipher,
    UpstreamError,
    ValidationError,
    secret_detection_message,
)
from app.domain.publication import (
    authorize_publication,
    build_frontmatter,
    build_revision_frontmatter,
    validate_playbook,
)
from app.domain.transcript import extract_command_outputs
from app.domain.audit import audit_event
from app.domain.credentials import digest_api_token
from app.domain.dlp import sanitize_secrets


logger = logging.getLogger("lucien.worker")

# Runbook sem conteúdo da SLM: o CLI preenche a estrutura básica e o operador
# redige objetivo, validação e rollback na revisão obrigatória.
_EMPTY_ENRICHMENT = RunbookEnrichment(
    inferred_tags=(),
    suggestions=RunbookSuggestions(
        objective="",
        architecture_prerequisites=(),
        command_impacts=(),
        rollback_commands=(),
    ),
)


def _normalize_display_name(value: str | None) -> str | None:
    """Nome completo do LDAP, saneado antes de virar autor do runbook.

    O valor vem do script do jump server e nao participa de autorizacao --
    `username` continua sendo a identidade. Mesmo assim ele e publicado, e
    conteudo publicado passa por checagem aqui: controle de tamanho, nada de
    caractere de controle e colapso de espacos.

    Quem chama nao pode confiar so nisso: o GECOS do POSIX e
    `Nome,sala,telefone,telefone`, entao mandar o campo inteiro colocaria
    telefone no runbook publicado. O recorte fica no script, que conhece o
    formato; aqui e a segunda barreira.
    """

    if value is None:
        return None
    limpo = " ".join(value.replace(chr(0), " ").split())
    limpo = "".join(c for c in limpo if c.isprintable())
    if not limpo:
        return None
    if len(limpo) > 120:
        raise ValidationError("display_name exceeds 120 characters")
    return limpo


class IdentityService:
    """Emite tokens e altera identidades sem expor detalhes de persistência."""

    _PROVISIONAL_TTL = timedelta(hours=4)

    def __init__(
        self,
        repository: IdentityRepository,
        auth_pepper: str,
        domain_functions: tuple[str, ...] = DEFAULT_DOMAIN_FUNCTIONS,
    ) -> None:
        self._repository = repository
        self._auth_pepper = auth_pepper
        # Mesma lista que autoriza `lucien start -r`. Se um usuario pudesse ser
        # criado em dominio fora dela, a publicacao implicita dele cairia num
        # diretorio que o administrador nunca declarou.
        self._domain_functions = domain_functions

    def _require_known_domain(self, domain_function: str) -> None:
        if domain_function not in self._domain_functions:
            disponiveis = ", ".join(self._domain_functions) or "(nenhuma configurada)"
            raise ValidationError(
                f"area '{domain_function}' does not exist; check the role. "
                f"Available: {disponiveis}"
            )

    async def bootstrap_admin(
        self, username: str, domain_function: str
    ) -> tuple[User, str]:
        api_token, token_hash = self._prepare_permanent_credentials(
            username, domain_function
        )
        user = await self._repository.create_bootstrap_admin(
            username, token_hash, domain_function
        )
        audit_event(
            "user.bootstrap",
            actor_id="bootstrap",
            target_id=user.id,
            target_username=user.username,
        )
        return user, api_token

    async def create_user(
        self,
        actor: SecurityContext,
        username: str,
        role_level: RoleLevel,
        domain_function: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[User, str, datetime]:
        self._require_admin(actor)
        self._validate_identity(username, domain_function)
        self._require_known_domain(domain_function)
        for dominio in extra_domains:
            if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", dominio) is None:
                raise ValidationError(f"invalid area '{dominio}'")
            self._require_known_domain(dominio)
        provisional_token, provisional_hash = self._new_provisional_token()
        expires_at = datetime.now(timezone.utc) + self._PROVISIONAL_TTL
        user = await self._repository.create_provisioned_user(
            username,
            provisional_hash,
            expires_at,
            role_level,
            domain_function,
            extra_domains,
        )
        audit_event(
            "user.create",
            actor_id=actor.user_id,
            target_id=user.id,
            target_username=user.username,
            role_level=role_level.value,
            domain_function=domain_function,
        )
        return user, provisional_token, expires_at

    async def get_user(self, actor: SecurityContext) -> User:
        return await self._repository.get_user(actor.user_id)

    async def issue_provisional_token(
        self,
        actor: SecurityContext,
        id_or_username: str,
        scope: str | None = None,
    ) -> tuple[User, str, datetime]:
        """Revoga o token perdido e emite ativação de uso único por quatro horas.

        `scope=None` preserva o comportamento de sempre. Um nome isola a
        revogação e a próxima troca naquele escopo -- por exemplo, emitir um
        provisório `scope="personal"` não mexe na credencial `"jump"`.
        """

        self._require_admin(actor)
        target = await self._repository.get_user_by_identifier(id_or_username)
        provisional_token, provisional_hash = self._new_provisional_token()
        expires_at = datetime.now(timezone.utc) + self._PROVISIONAL_TTL
        user = await self._repository.issue_provisional_token(
            target.id, provisional_hash, expires_at, scope=scope
        )
        audit_event(
            "user.issue_provisional_token",
            actor_id=actor.user_id,
            target_id=user.id,
            target_username=user.username,
        )
        return user, provisional_token, expires_at

    async def exchange_provisional_token(
        self, provisional_token: str, idempotency_key: str
    ) -> tuple[User, str]:
        """Troca atomicamente uma ativação temporária por token permanente."""

        if not provisional_token.startswith("luc_tmp_"):
            raise ValidationError("invalid provisional token format")
        if not 8 <= len(idempotency_key) <= 128:
            raise ValidationError("invalid Idempotency-Key")
        provisional_hash = digest_api_token(provisional_token, self._auth_pepper)
        api_token = self._derive_permanent_token(
            provisional_token, idempotency_key
        )
        api_token_hash = digest_api_token(api_token, self._auth_pepper)
        idempotency_key_hash = digest_api_token(
            f"exchange:{idempotency_key}", self._auth_pepper
        )
        user = await self._repository.exchange_provisional_token(
            provisional_hash,
            api_token_hash,
            idempotency_key_hash,
            datetime.now(timezone.utc),
        )
        audit_event(
            "user.exchange_provisional_token",
            actor_id=user.id,
            target_id=user.id,
            target_username=user.username,
        )
        return user, api_token

    async def enroll_jump_user(
        self,
        username: str,
        domain_function: str | None,
        idempotency_key: str,
        display_name: str | None = None,
    ) -> tuple[User, str, datetime, str | None]:
        """Provisiona identidade POSIX sem conceder autoridade administrativa.

        O provisório trocado por este fluxo sempre grava em `scope="jump"` --
        nunca disputa a coluna legada nem uma credencial pessoal de outro
        escopo. No primeiro acesso desta identidade (jump-criada ou não), uma
        credencial permanente `scope="personal"` é emitida e devolvida uma
        única vez no quarto elemento da tupla (`None` quando já existia).
        """

        if re.fullmatch(r"[A-Za-z][0-9]+", username) is None:
            raise ValidationError("invalid jump server username")
        display_name = _normalize_display_name(display_name)
        if domain_function is not None:
            self._require_known_domain(domain_function)
        if not 8 <= len(idempotency_key) <= 128:
            raise ValidationError("invalid Idempotency-Key")

        expires_at = datetime.now(timezone.utc) + self._PROVISIONAL_TTL

        try:
            user = await self._repository.get_user_by_identifier(username)
        except NotFoundError:
            if domain_function is None:
                raise ValidationError(
                    "user not registered; provide domain_function"
                )
            provisional_token = self._derive_jump_provisional_token(
                username, domain_function, idempotency_key
            )
            provisional_hash = digest_api_token(
                provisional_token, self._auth_pepper
            )
            try:
                user = await self._repository.create_provisioned_user(
                    username,
                    provisional_hash,
                    expires_at,
                    RoleLevel.PLENO,
                    domain_function,
                    display_name=display_name,
                    scope="jump",
                )
                audit_event(
                    "user.jump_enroll",
                    actor_id="jump-enrollment",
                    target_id=user.id,
                    target_username=user.username,
                    role_level=RoleLevel.PLENO.value,
                    domain_function=domain_function,
                )
                personal_token = await self._issue_personal_token_if_absent(user)
                return user, provisional_token, expires_at, personal_token
            except ConflictError:
                # Outra requisição idempotente pode ter criado a identidade.
                user = await self._repository.get_user_by_identifier(username)

        if not user.is_active:
            raise ConflictError("a revoked user cannot be reactivated by the jump server")
        if user.role_level is RoleLevel.ADMIN:
            raise ConflictError(
                "an administrator must use the administrative login flow"
            )
        if domain_function is not None and user.domain_function != domain_function:
            raise ConflictError(
                "the existing user has a different domain; ask an admin"
            )
        effective_domain = user.domain_function
        provisional_token = self._derive_jump_provisional_token(
            username, effective_domain, idempotency_key
        )
        provisional_hash = digest_api_token(provisional_token, self._auth_pepper)
        user = await self._repository.issue_provisional_token(
            user.id, provisional_hash, expires_at, display_name, scope="jump"
        )
        audit_event(
            "user.jump_reissue",
            actor_id="jump-enrollment",
            target_id=user.id,
            target_username=user.username,
            role_level=user.role_level.value,
            domain_function=user.domain_function,
        )
        personal_token = await self._issue_personal_token_if_absent(user)
        return user, provisional_token, expires_at, personal_token

    async def _issue_personal_token_if_absent(self, user: User) -> str | None:
        """Dá à identidade jump uma chave utilizável fora dele, uma única vez.

        `has_user_credential` é o gate, não a criação-vs-reemissão: cobre
        tanto quem acabou de ser criado pelo jump quanto quem já existia e
        está logando por ele pela primeira vez.
        """

        if await self._repository.has_user_credential(user.id, "personal"):
            return None
        personal_token, personal_hash = self._new_permanent_token()
        try:
            await self._repository.issue_permanent_credential(
                user.id, "personal", personal_hash
            )
        except ConflictError:
            # Corrida com outra requisição idempotente: a credencial já existe.
            return None
        audit_event(
            "user.personal_credential_issued",
            actor_id="jump-enrollment",
            target_id=user.id,
            target_username=user.username,
        )
        return personal_token

    async def recover_admin_token(
        self, id_or_username: str
    ) -> tuple[User, str, datetime]:
        """Recupera offline um admin; nunca deve ser exposto por rota HTTP."""

        target = await self._repository.get_user_by_identifier(id_or_username)
        if target.role_level is not RoleLevel.ADMIN or not target.is_active:
            raise ForbiddenError("recovery requires an active administrator")
        provisional_token, provisional_hash = self._new_provisional_token()
        expires_at = datetime.now(timezone.utc) + self._PROVISIONAL_TTL
        user = await self._repository.issue_provisional_token(
            target.id, provisional_hash, expires_at
        )
        audit_event(
            "user.recover_provisional_token",
            actor_id="local-console",
            target_id=user.id,
            target_username=user.username,
        )
        return user, provisional_token, expires_at

    async def update_scopes(
        self,
        actor: SecurityContext,
        id_or_username: str,
        role_level: RoleLevel | None,
        domain_function: str | None,
        extra_domains: tuple[str, ...] | None = None,
    ) -> User:
        self._require_admin(actor)
        target = await self._repository.get_user_by_identifier(id_or_username)
        if actor.user_id == target.id:
            raise ForbiddenError("an admin cannot change their own scope")
        if domain_function is not None:
            if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", domain_function) is None:
                raise ValidationError("invalid domain_function")
            self._require_known_domain(domain_function)
        if extra_domains is not None:
            # Cada area concedida passa pela mesma checagem da primaria: uma
            # area fora de RUNBOOK_DOMAIN_FUNCTIONS viraria um diretorio que o
            # administrador nunca declarou.
            for dominio in extra_domains:
                if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", dominio) is None:
                    raise ValidationError(f"invalid area '{dominio}'")
                self._require_known_domain(dominio)
        user = await self._repository.update_user_scopes(
            target.id, role_level, domain_function, extra_domains
        )
        audit_event(
            "user.update_scopes",
            actor_id=actor.user_id,
            target_id=user.id,
            role_level=user.role_level.value,
            domain_function=user.domain_function,
        )
        return user

    async def revoke_user(
        self, actor: SecurityContext, id_or_username: str
    ) -> None:
        self._require_admin(actor)
        target = await self._repository.get_user_by_identifier(id_or_username)
        if actor.user_id == target.id:
            raise ForbiddenError("an admin cannot revoke their own token")
        # A recusa do último admin mora no repositório, junto da gravação:
        # contar aqui e gravar depois deixa a janela em que dois admins se
        # revogam ao mesmo tempo e ambos veem dois na contagem.
        await self._repository.revoke_user(target.id)
        audit_event(
            "user.revoke", actor_id=actor.user_id, target_id=target.id
        )

    def _prepare_permanent_credentials(
        self, username: str, domain_function: str
    ) -> tuple[str, str]:
        self._validate_identity(username, domain_function)
        return self._new_permanent_token()

    def _validate_identity(self, username: str, domain_function: str) -> None:
        if re.fullmatch(r"[a-zA-Z0-9_.-]{3,64}", username) is None:
            raise ValidationError("invalid username")
        if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", domain_function) is None:
            raise ValidationError("invalid domain_function")

    def _new_permanent_token(self) -> tuple[str, str]:
        # Token aleatório de alta entropia; somente o HMAC com pepper chega ao banco.
        api_token = f"luc_{secrets.token_urlsafe(32)}"
        token_hash = digest_api_token(api_token, self._auth_pepper)
        return api_token, token_hash

    def _new_provisional_token(self) -> tuple[str, str]:
        token = f"luc_tmp_{secrets.token_urlsafe(32)}"
        return token, digest_api_token(token, self._auth_pepper)

    def _derive_permanent_token(
        self, provisional_token: str, idempotency_key: str
    ) -> str:
        """Permite retry da mesma troca sem persistir o token recuperável."""

        digest = hmac.new(
            self._auth_pepper.encode(),
            f"exchange\0{provisional_token}\0{idempotency_key}".encode(),
            hashlib.sha256,
        ).digest()
        encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return f"luc_{encoded}"

    def _derive_jump_provisional_token(
        self, username: str, domain_function: str, idempotency_key: str
    ) -> str:
        """Reconcilia retries sem armazenar o token provisório recuperável."""

        digest = hmac.new(
            self._auth_pepper.encode(),
            (
                f"jump-enroll\0{username}\0{domain_function}\0"
                f"{idempotency_key}"
            ).encode(),
            hashlib.sha256,
        ).digest()
        encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return f"luc_tmp_{encoded}"

    @staticmethod
    def _require_admin(actor: SecurityContext) -> None:
        if actor.role_level is not RoleLevel.ADMIN:
            raise ForbiddenError("admin-only operation")


class UploadService:
    """Aceita uploads rapidamente e transfere a custódia para a fila cifrada."""

    def __init__(
        self,
        repository: JobRepository,
        secret_scanner: SecretScanner,
        cipher: UploadCipher,
        max_log_bytes: int,
        domain_functions: tuple[str, ...] = DEFAULT_DOMAIN_FUNCTIONS,
    ) -> None:
        self._repository = repository
        self._secret_scanner = secret_scanner
        self._cipher = cipher
        self._max_log_bytes = max_log_bytes
        self._domain_functions = domain_functions

    def _resolve_domain_function(
        self, context: SecurityContext, requested: str | None
    ) -> str | None:
        """Valida `lucien start -r` contra a lista do .env e o escopo do autor.

        Devolve `None` quando nada foi pedido: a publicacao segue usando o
        dominio do autor, como sempre fez.
        """

        if requested is None:
            return None
        if requested not in self._domain_functions:
            disponiveis = ", ".join(self._domain_functions) or "(nenhuma configurada)"
            raise ValidationError(
                f"area '{requested}' does not exist; check the role given in -r. "
                f"Available: {disponiveis}"
            )
        # A area continua sendo escopo de autoridade, nao preferencia: publica
        # quem foi autorizado. A autorizacao e que passou a poder cobrir mais
        # de uma area, concedida pelo admin.
        if not context.authorizes(requested):
            autorizadas = ", ".join(
                sorted({context.domain_function, *context.extra_domains})
            )
            raise ForbiddenError(
                f"area '{requested}' is outside your scope. "
                f"Authorized: {autorizadas}"
            )
        return requested

    async def enqueue(
        self,
        context: SecurityContext,
        name: str,
        raw_log: str,
        description: str | None = None,
        skip_enrichment: bool = False,
        domain_function: str | None = None,
    ) -> Job:
        owner_id = context.user_id
        # Antes de qualquer trabalho caro: recusar aqui evita cifrar e enfileirar
        # um log que nunca seria publicado.
        resolved_domain = self._resolve_domain_function(context, domain_function)
        if len(raw_log.encode("utf-8")) > self._max_log_bytes:
            raise ConflictError("the log exceeds the configured limit")

        normalized_description = " ".join((description or "").split())
        if len(normalized_description) > 280:
            raise ValidationError("description must be at most 280 characters")

        # Vazio é um runbook puramente visual (ver process_once) -- o scanner
        # exige conteúdo não vazio e recusaria a chamada, não a ausência dele.
        if raw_log:
            await self._reject_detected_secret(raw_log)
        if normalized_description:
            await self._reject_detected_secret(normalized_description)
        sanitized_log = sanitize_secrets(raw_log).text
        sanitized_description = (
            sanitize_secrets(normalized_description).text
            if normalized_description
            else None
        )
        sealed = self._cipher.seal(
            owner_id, name, sanitized_log, sanitized_description
        )
        job = await self._repository.enqueue_job(
            owner_id,
            name,
            sealed.fingerprint,
            sealed.ciphertext,
            skip_enrichment,
            resolved_domain,
        )
        audit_event("job.enqueue", actor_id=owner_id, job_id=job.id)
        return job

    async def retry(
        self,
        owner_id: str,
        id_or_name: str,
        skip_enrichment: bool | None = None,
    ) -> Job:
        job = await self._repository.retry_failed_upload(
            owner_id, id_or_name, datetime.now(timezone.utc), skip_enrichment
        )
        audit_event("job.retry", actor_id=owner_id, job_id=job.id)
        return job

    async def _reject_detected_secret(self, content: str) -> None:
        resultado = await self._secret_scanner.detect(content)
        if resultado.detected:
            raise SecretDetectedError(_mensagem_de_segredo(resultado))


class UploadProcessor:
    """Consome um item por vez; leases no repositório permitem múltiplas réplicas."""

    def __init__(
        self,
        repository: JobRepository,
        cipher: UploadCipher,
        extractor: CommandExtractor,
        tag_inferrer: RunbookEnricher,
        secret_scanner: SecretScanner,
        lease_seconds: int,
        retry_base_seconds: int,
        max_attempts: int,
        enrichment_enabled: bool = True,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._extractor = extractor
        self._tag_inferrer = tag_inferrer
        self._secret_scanner = secret_scanner
        self._lease = timedelta(seconds=lease_seconds)
        self._retry_base_seconds = retry_base_seconds
        self._max_attempts = max_attempts
        self._enrichment_enabled = enrichment_enabled

    async def process_once(self) -> bool:
        now = datetime.now(timezone.utc)
        queued = await self._repository.claim_next_upload(now, now + self._lease)
        if queued is None:
            return False

        try:
            sanitized_log, sanitized_description = self._cipher.open(
                queued.owner_id, queued.name, queued.ciphertext
            )
            # Log vazio é um runbook puramente visual, sem sessão de terminal --
            # a extração nem roda, então "nada encontrado" nunca é confundido
            # com a falha real de extrair de um log que existe.
            if sanitized_log.strip():
                extracted = await self._extractor.extract(
                    sanitized_log, sanitized_description
                )
                if extracted:
                    await self._reject_detected_secret("\n".join(extracted))
                commands = tuple(
                    sanitize_secrets(command).text for command in extracted
                )
                if not commands:
                    raise ConflictError("no useful command was detected")
                command_outputs = tuple(
                    sanitize_secrets(output).text
                    for output in extract_command_outputs(sanitized_log, commands)
                )
            else:
                commands = ()
                command_outputs = ()
            enrichment = await self._enrich_or_fallback(
                commands,
                sanitized_description,
                queued.owner_id,
                queued.job_id,
                queued.skip_enrichment,
            )
            job = await self._repository.complete_upload(
                queued.job_id,
                commands,
                command_outputs,
                enrichment.suggestions,
                enrichment.inferred_tags,
                sanitize_secrets(sanitized_description or "").text,
            )
            audit_event("job.ready", actor_id=job.owner_id, job_id=job.id)
        except NotFoundError:
            # O proprietário pode cancelar um Job enquanto a SLM ainda trabalha.
            # A remoção transacional do Job também elimina o payload da fila.
            audit_event(
                "job.cancelled",
                actor_id=queued.owner_id,
                job_id=queued.job_id,
            )
        except SecretDetectedError:
            await self._mark_failed(
                queued.owner_id, queued.job_id, "SECRET_DETECTED"
            )
        except ConflictError:
            await self._mark_failed(queued.owner_id, queued.job_id, "NO_COMMANDS")
        except ValidationError:
            await self._mark_failed(
                queued.owner_id, queued.job_id, "PAYLOAD_INVALID"
            )
        except UpstreamError:
            await self._retry_or_fail(
                queued.owner_id, queued.job_id, queued.attempts, "UPSTREAM"
            )
        except Exception as error:  # Falha inesperada sem registrar conteúdo ou payload.
            logger.error(
                "falha inesperada no worker job_id=%s tipo=%s",
                queued.job_id,
                type(error).__name__,
            )
            await self._retry_or_fail(
                queued.owner_id, queued.job_id, queued.attempts, "INTERNAL"
            )
        return True

    async def _enrich_or_fallback(
        self,
        commands: tuple[str, ...],
        sanitized_description: str | None,
        actor_id: str,
        job_id: str,
        skip_enrichment: bool = False,
    ) -> RunbookEnrichment:
        """Enriquecimento é auxiliar: sua ausência não invalida o Job.

        O runbook continua utilizável sem objetivo, impactos ou rollback
        sugeridos — o CLI já emite a estrutura básica e o operador redige na
        revisão obrigatória. Falhar o Job por causa de conteúdo não autoritativo
        descartaria a extração, que é a parte insubstituível do trabalho.
        """

        if skip_enrichment or not self._enrichment_enabled:
            return _EMPTY_ENRICHMENT
        try:
            enrichment = await self._tag_inferrer.infer(
                commands, sanitized_description
            )
        except UpstreamError:
            audit_event(
                "job.enrichment_skipped", actor_id=actor_id, job_id=job_id
            )
            return _EMPTY_ENRICHMENT
        return await self._sanitize_enrichment(enrichment, len(commands))

    async def _retry_or_fail(
        self, actor_id: str, job_id: str, attempts: int, error_prefix: str
    ) -> None:
        if attempts >= self._max_attempts:
            await self._mark_failed(actor_id, job_id, f"{error_prefix}_ERROR")
            return
        exponent = min(max(attempts - 1, 0), 6)
        delay = min(self._retry_base_seconds * (2**exponent), 300)
        rescheduled = await self._repository.reschedule_upload(
            job_id, datetime.now(timezone.utc) + timedelta(seconds=delay)
        )
        if not rescheduled:
            audit_event("job.cancelled", actor_id=actor_id, job_id=job_id)
            return
        audit_event(
            "job.reschedule",
            actor_id=actor_id,
            job_id=job_id,
            attempts=str(attempts),
        )

    async def _mark_failed(
        self, actor_id: str, job_id: str, error_code: str
    ) -> None:
        failed = await self._repository.fail_upload(job_id, error_code)
        if not failed:
            audit_event("job.cancelled", actor_id=actor_id, job_id=job_id)
            return
        audit_event(
            "job.failed", actor_id=actor_id, job_id=job_id, error_code=error_code
        )

    async def _reject_detected_secret(self, content: str) -> None:
        resultado = await self._secret_scanner.detect(content)
        if resultado.detected:
            raise SecretDetectedError(_mensagem_de_segredo(resultado))

    async def _sanitize_enrichment(
        self, enrichment: RunbookEnrichment, command_count: int
    ) -> RunbookEnrichment:
        suggestions = enrichment.suggestions
        sensitive_surface = "\n".join(
            (
                suggestions.objective,
                *suggestions.architecture_prerequisites,
                *suggestions.command_impacts,
                *suggestions.rollback_commands,
            )
        )
        if sensitive_surface:
            await self._reject_detected_secret(sensitive_surface)

        impacts = list(suggestions.command_impacts[:command_count])
        impacts.extend("" for _ in range(command_count - len(impacts)))
        return RunbookEnrichment(
            inferred_tags=enrichment.inferred_tags,
            suggestions=RunbookSuggestions(
                objective=sanitize_secrets(suggestions.objective).text,
                architecture_prerequisites=tuple(
                    sanitize_secrets(item).text
                    for item in suggestions.architecture_prerequisites
                ),
                command_impacts=tuple(
                    sanitize_secrets(item).text for item in impacts
                ),
                rollback_commands=tuple(
                    sanitize_secrets(item).text
                    for item in suggestions.rollback_commands
                ),
            ),
        )


class JobService:
    """Orquestra casos de uso sem conhecer FastAPI, SQLAlchemy ou Ollama."""

    _REVISION_RESERVATION_TTL = timedelta(minutes=15)
    _PUBLISHED_CATALOG_LIMIT = 10_000

    def __init__(
        self,
        repository: JobRepository,
        secret_scanner: SecretScanner,
        storage: StorageProvider,
        image_scanner: ImageSecurityScanner | None = None,
        revisions_enabled: bool = False,
        entry_roles_enabled: bool = False,
        max_assets_per_publication: int = 20,
    ) -> None:
        self._repository = repository
        self._secret_scanner = secret_scanner
        self._storage = storage
        self._image_scanner = image_scanner
        self._revisions_enabled = revisions_enabled
        self._entry_roles_enabled = entry_roles_enabled
        self._max_assets_per_publication = max_assets_per_publication

    async def list_pending(self, owner_id: str) -> list[Job]:
        return await self._repository.list_pending(owner_id)

    async def list_active(self, owner_id: str) -> list[Job]:
        return await self._repository.list_active(owner_id)

    async def get_job(self, owner_id: str, id_or_name: str) -> Job:
        return await self._repository.get_job(owner_id, id_or_name)

    async def list_published_runbook_ids(self) -> tuple[str, ...]:
        return await self._repository.list_published_runbook_ids(
            self._PUBLISHED_CATALOG_LIMIT
        )

    async def list_published_runbooks_for(
        self, context: SecurityContext
    ) -> tuple[tuple[str, str], ...]:
        """Pares (id, nome) que `context` esta autorizado a revisar de verdade.

        Admin nao tem filtro (ja cruza qualquer area, igual `authorizes`);
        os demais so veem publicacoes cujo dominio congelado bate com
        `authorized_domains` -- a mesma checagem que `revise` aplicaria depois,
        so que antes, pra nao listar algo que o clique seguinte recusaria.
        """

        allowed_domains = (
            None
            if context.role_level is RoleLevel.ADMIN
            else tuple(context.authorized_domains)
        )
        return await self._repository.list_published_runbooks_for_domains(
            allowed_domains, self._PUBLISHED_CATALOG_LIMIT
        )

    async def _scan_and_reencode_assets(
        self,
        markdown: str,
        known_job_id: str,
        raw_assets: tuple[RawAssetInput, ...],
        already_existing_paths: frozenset[str] = frozenset(),
    ) -> dict[str, ProcessedAsset]:
        """Roda o gate de imagem inteiro: referencia -> OCR -> segredo.

        Deliberadamente ANTES de qualquer reserva de DB, na mesma posicao dos
        gates de texto acima -- uma imagem recusada nunca deve tocar o
        repositorio, igual a um segredo de texto detectado hoje.

        `known_job_id` e o id que o CLIENTE usou ao escrever
        `assets/<job_id>/...` no corpo -- para `publish` e o job ja existente
        (resolvido antes desta chamada); para `revise`, o job de origem. Pode
        nao ser o `job.id` final da reserva (uma revisao sempre cria um id
        novo) -- por isso o caminho e reescrito depois, em
        `_rewrite_asset_paths`, com o id real.

        `already_existing_paths` (so populado por `revise`) sao imagens
        herdadas sem alteracao da versao publicada anterior: nao precisam
        vir em `raw_assets` de novo, so continuar referenciadas como texto.
        """

        if self._image_scanner is None:
            # So dispara se alguem construir o JobService sem scanner de
            # imagem e ainda assim tentar publicar com anexo -- erro de
            # configuracao, nao entrada de usuario. Em producao main.py
            # sempre injeta um TesseractImageScanner real.
            raise RuntimeError("this JobService instance has no image scanner configured")
        if len(raw_assets) > self._max_assets_per_publication:
            raise ValidationError(
                "a publication accepts at most "
                f"{self._max_assets_per_publication} images"
            )
        references = extract_asset_references(markdown, known_job_id, already_existing_paths)
        submitted_filenames = frozenset(asset.filename for asset in raw_assets)
        validate_asset_completeness(references, submitted_filenames, already_existing_paths)

        processed: dict[str, ProcessedAsset] = {}
        for asset in raw_assets:
            try:
                raw_bytes = base64.b64decode(asset.content_base64, validate=True)
            except ValueError as error:
                raise ValidationError(
                    f"asset '{asset.filename}' has invalid base64 content"
                ) from error
            try:
                processed[asset.filename] = await self._image_scanner.process(
                    raw_bytes, asset.media_type
                )
            except SecretDetectedError as error:
                # O texto vem de OCR, e nao esta a vista de quem escreveu o
                # runbook. Sem o nome do arquivo, a recusa parece apontar para
                # o markdown -- onde nao ha nada errado -- e o operador procura
                # no lugar errado. Um print de tela de login rende "Senha"
                # seguido do proximo rotulo, o que basta para a regra casar.
                #
                # O nome do arquivo e do proprio operador, nao do conteudo
                # detectado: dize-lo nao afrouxa a regra de nunca expor o valor.
                raise SecretDetectedError(
                    f"image '{asset.filename}': {error}"
                ) from error
        return processed

    @staticmethod
    def _rewrite_asset_paths(
        markdown: str, job_id: str, processed: dict[str, ProcessedAsset]
    ) -> tuple[str, tuple[AssetToPublish, ...]]:
        """Atribui nome opaco a cada asset e aponta o Markdown para o job real.

        Puro string-replace: nada aqui pode rejeitar a publicacao -- o gate
        que pode recusar ja rodou em `_scan_and_reencode_assets`.
        """

        filename_map = {
            client_filename: f"{uuid.uuid4().hex}.png"
            for client_filename in sorted(processed)
        }
        rewritten = rewritten_markdown(markdown, job_id, filename_map)
        assets_to_publish = tuple(
            AssetToPublish(
                filename=filename_map[client_filename],
                content=processed[client_filename].content,
            )
            for client_filename in sorted(processed)
        )
        return rewritten, assets_to_publish

    @staticmethod
    def _content_hash(markdown: str, assets: tuple[RawAssetInput, ...]) -> str:
        """Cobre texto e imagem, sobre o conteudo COMO ENVIADO, nunca o reescrito.

        Calculado antes de `_rewrite_asset_paths`, que atribui nome opaco via
        `uuid.uuid4()` -- um valor novo a cada chamada. Hashear o resultado
        reescrito faria a MESMA resubmissao bater um hash diferente toda vez,
        quebrando a idempotencia de `reserve_publication`/`reserve_revision`.
        Ordem estavel (por nome de arquivo do cliente) garante que o mesmo
        conjunto logico de anexos bate o mesmo hash em qualquer ordem de envio.
        """

        hasher = hashlib.sha256()
        hasher.update(markdown.encode("utf-8"))
        for asset in sorted(assets, key=lambda item: item.filename):
            hasher.update(b"\x00")
            hasher.update(asset.filename.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(asset.content_base64.encode("ascii"))
        return hasher.hexdigest()

    async def publish(
        self,
        context: SecurityContext,
        id_or_name: str,
        markdown: str,
        idempotency_key: str,
        assets: tuple[RawAssetInput, ...] = (),
    ) -> tuple[Job, int]:
        # O editor é não confiável: scanner bloqueia e DLP redige defesas residuais.
        await self._reject_detected_secret(markdown)
        sanitized = sanitize_secrets(markdown)
        validated = validate_playbook(sanitized.text)
        authorize_publication(
            context.role_level, validated.criticality, self._entry_roles_enabled
        )

        final_body = validated.body
        processed: dict[str, ProcessedAsset] = {}
        if assets:
            known_job = await self._repository.get_job(context.user_id, id_or_name)
            processed = await self._scan_and_reencode_assets(
                final_body, known_job.id, assets
            )

        # Hash sobre o conteudo COMO ENVIADO -- ver docstring de _content_hash.
        content_hash = self._content_hash(final_body, assets)
        job = await self._repository.reserve_publication(
            context.user_id,
            id_or_name,
            content_hash,
            idempotency_key,
            PublicationIdentity.from_context(context),
        )
        if job.status.value == "PUBLISHED":
            return job, sanitized.replacements

        if job.publication_identity is None:
            raise RuntimeError("publication reservation without a trusted identity")

        prepared_assets: tuple[AssetToPublish, ...] = ()
        if assets:
            final_body, prepared_assets = self._rewrite_asset_paths(
                final_body, job.id, processed
            )
            validated = dataclasses.replace(validated, body=final_body)

        document = build_frontmatter(job, job.publication_identity, validated)
        # `assets` so entra na chamada quando ha algo a publicar: um
        # StorageProvider de teste antigo, sem esse parametro na assinatura,
        # nao pode quebrar so porque o caso sem imagem continua existindo.
        # (branch explicito em vez de **kwargs: mypy nao valida kwargs de um
        # dict[str, object] contra a assinatura tipada do port.)
        if prepared_assets:
            artifact = await self._storage.publish(
                job.id,
                job.created_at,
                document,
                artifact_name=job.name,
                domain_function=job.publication_identity.domain_function,
                assets=prepared_assets,
            )
        else:
            artifact = await self._storage.publish(
                job.id,
                job.created_at,
                document,
                artifact_name=job.name,
                domain_function=job.publication_identity.domain_function,
            )
        published = await self._repository.mark_published(
            context.user_id,
            job.id,
            artifact.url,
            content_hash,
            idempotency_key,
        )
        audit_event(
            "job.publish",
            actor_id=context.user_id,
            job_id=published.id,
            criticality=validated.criticality.value,
            storage_url=artifact.url,
        )
        return published, sanitized.replacements

    async def published_content(
        self, context: SecurityContext, job_id: str
    ) -> tuple[str, str]:
        """Devolve o corpo revisavel e o hash que servira de If-Match.

        O frontmatter e removido de proposito: ele e gerado pelo Hub e o
        `revise` rejeita frontmatter vindo do cliente. Devolve-lo convidaria
        o operador a cola-lo de volta e receber erro de validacao.
        """

        if not self._revisions_enabled:
            raise ForbiddenError("revision is unavailable on this provider")
        self._require_revision_role(context)
        revision_source = await self._repository.get_published_for_revision(job_id)
        source = revision_source.job
        self._require_revision_domain(context, revision_source)
        if source.content_hash is None:
            raise ConflictError("publication without a trusted content hash")

        publicado = await self._storage.read_published(
            source.id,
            source.created_at,
            artifact_name=source.name,
            domain_function=revision_source.root_identity.domain_function,
        )
        return _strip_frontmatter(publicado), source.content_hash

    def _require_revision_role(self, context: SecurityContext) -> None:
        allowed_roles = {RoleLevel.SENIOR, RoleLevel.ADMIN}
        if self._entry_roles_enabled:
            allowed_roles |= {RoleLevel.JUNIOR, RoleLevel.PLENO}
        if context.role_level not in allowed_roles:
            raise ForbiddenError("senior or admin only operation")

    def _require_revision_domain(
        self, context: SecurityContext, revision_source: RevisionSource
    ) -> None:
        # Fora do dominio autorizado responde 404, e nao 403: confirmar a
        # existencia ja seria vazamento. Quem publica numa area tambem revisa
        # nela -- as duas operacoes gravam no mesmo diretorio e passam pelas
        # mesmas camadas do Hub.
        if not context.authorizes(revision_source.root_identity.domain_function):
            # Byte a byte igual à recusa do repositório para fonte inexistente.
            # Divergir aqui -- ainda que só por um acento -- deixaria distinguir
            # "não existe" de "existe e não é seu", que é o que esta recusa
            # existe para esconder. A diferença fica na trilha.
            raise NotFoundError("published runbook not found")

    def _registrar_revisao_negada(
        self,
        context: SecurityContext,
        source_job_id: str,
        motivo: str,
        **extras: str,
    ) -> None:
        """Registra qual das recusas ocorreu, sem mudar o que o cliente vê.

        "Não existe" e "existe em área que você não alcança" respondem a mesma
        coisa de propósito: distinguir já confirmaria a existência. Mas quem
        investiga precisa da diferença, e o lugar dela é a trilha -- alcançável
        pelo `request_id` que acompanha a resposta.
        """
        audit_event(
            "runbook.revise_negada",
            actor_id=context.user_id,
            source_job_id=source_job_id,
            motivo=motivo,
            **extras,
        )

    async def revise(
        self,
        context: SecurityContext,
        source_job_id: str,
        expected_content_hash: str,
        markdown: str,
        idempotency_key: str,
        assets: tuple[RawAssetInput, ...] = (),
    ) -> tuple[Job, int]:
        """Publica um sucessor imutável sem alterar a versão fonte."""

        if not self._revisions_enabled:
            raise ConflictError(
                "revisions are disabled in this installation"
            )
        self._require_revision_role(context)
        if re.fullmatch(r"[0-9a-f]{64}", expected_content_hash) is None:
            raise ValidationError("If-Match contains an invalid hash")

        try:
            revision_source = await self._repository.get_published_for_revision(
                source_job_id
            )
        except NotFoundError:
            self._registrar_revisao_negada(
                context, source_job_id, "fonte_inexistente_ou_nao_publicada"
            )
            raise
        try:
            self._require_revision_domain(context, revision_source)
        except NotFoundError:
            self._registrar_revisao_negada(
                context,
                source_job_id,
                "fora_do_dominio",
                dominio_do_runbook=revision_source.root_identity.domain_function,
                dominio_do_ator=context.domain_function,
            )
            raise

        # O formulário web é tão não confiável quanto o editor do CLI.
        await self._reject_detected_secret(markdown)
        sanitized = sanitize_secrets(markdown)
        validated = validate_playbook(sanitized.text)
        authorize_publication(
            context.role_level, validated.criticality, self._entry_roles_enabled
        )

        final_body = validated.body
        processed: dict[str, ProcessedAsset] = {}
        if assets:
            # O id conhecido do cliente aqui e o da fonte -- a revisao ainda
            # nao tem id proprio antes de `reserve_revision`. O caminho final
            # em disco usa o id real da revisao, atribuido depois.
            #
            # Uma imagem herdada sem alteracao da versao publicada anterior
            # nao vem em `assets` -- so releio o corpo ja publicado pra saber
            # quais referencias ja existiam e podem ficar de fora da checagem
            # de completude, em vez de forcar reenvio de algo que nao mudou.
            previous_body = _strip_frontmatter(
                await self._storage.read_published(
                    revision_source.job.id,
                    revision_source.job.created_at,
                    artifact_name=revision_source.job.name,
                    domain_function=revision_source.root_identity.domain_function,
                )
            )
            already_existing_paths = previously_published_asset_paths(previous_body)
            processed = await self._scan_and_reencode_assets(
                final_body, source_job_id, assets, already_existing_paths
            )

        content_hash = self._content_hash(final_body, assets)
        revision = await self._repository.reserve_revision(
            context.user_id,
            source_job_id,
            expected_content_hash,
            content_hash,
            idempotency_key,
            PublicationIdentity.from_context(context),
            validated.command_blocks,
            datetime.now(timezone.utc) - self._REVISION_RESERVATION_TTL,
        )
        if revision.status.value == "PUBLISHED":
            return revision, sanitized.replacements
        if (
            revision.publication_identity is None
            or revision.content_hash is None
            or revision.idempotency_key is None
        ):
            raise RuntimeError("revision reservation without a trusted identity")

        prepared_assets: tuple[AssetToPublish, ...] = ()
        if assets:
            final_body, prepared_assets = self._rewrite_asset_paths(
                final_body, revision.id, processed
            )
            validated = dataclasses.replace(validated, body=final_body)

        document = build_revision_frontmatter(
            revision, revision.publication_identity, validated
        )
        if prepared_assets:
            artifact = await self._storage.publish(
                revision.id,
                revision.created_at,
                document,
                artifact_name=revision.name,
                domain_function=revision_source.root_identity.domain_function,
                assets=prepared_assets,
            )
        else:
            artifact = await self._storage.publish(
                revision.id,
                revision.created_at,
                document,
                artifact_name=revision.name,
                domain_function=revision_source.root_identity.domain_function,
            )
        published = await self._repository.mark_revision_published(
            revision.owner_id,
            revision.id,
            artifact.url,
            revision.content_hash,
            revision.idempotency_key,
        )
        audit_event(
            "runbook.revise",
            actor_id=context.user_id,
            root_job_id=published.root_job_id or "",
            source_job_id=published.supersedes_job_id or "",
            revision_job_id=published.id,
            revision_number=str(published.revision_number),
            criticality=validated.criticality.value,
            storage_url=artifact.url,
        )
        return published, sanitized.replacements

    async def delete(
        self, owner_id: str, id_or_name: str, force: bool = False
    ) -> None:
        deleted = await self._repository.delete_job(owner_id, id_or_name, force)
        audit_event(
            "job.delete",
            actor_id=owner_id,
            target=deleted.id,
            previous_status=deleted.status.value,
            forced=str(force).lower(),
        )

    async def _reject_detected_secret(self, content: str) -> None:
        resultado = await self._secret_scanner.detect(content)
        if resultado.detected:
            raise SecretDetectedError(_mensagem_de_segredo(resultado))


# Movida para o dominio (`secret_detection_message`) porque o gate de imagem
# tambem precisa dela e nao pode depender da camada de aplicacao. O alias
# preserva o nome usado nas chamadas internas deste modulo.
_mensagem_de_segredo = secret_detection_message


def _strip_frontmatter(markdown: str) -> str:
    """Remove o bloco YAML inicial gerado pelo Hub, preservando o corpo."""

    normalizado = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if not normalizado.startswith("---\n"):
        return normalizado
    fechamento = normalizado.find("\n---\n", 4)
    if fechamento == -1:
        return normalizado
    corpo = normalizado[fechamento + len("\n---\n"):]
    return corpo.lstrip("\n")
