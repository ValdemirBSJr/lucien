import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.models import (
    Job,
    JobStatus,
    PublicationIdentity,
    QueuedUpload,
    RevisionSource,
    RoleLevel,
    RunbookSuggestions,
    User,
)
from app.infrastructure.migrations import (
    aplicar_migracoes,
    registrar_esquema_do_modelo,
)
from app.infrastructure.storage import revision_artifact_name
from app.domain.ports import (
    AuthenticationError,
    ConflictError,
    IdentityRepository,
    JobRepository,
    MirroredAsset,
    MirroredDocument,
    NotFoundError,
    PreconditionFailedError,
    PublishedMirror,
)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role_level IN ('junior', 'pleno', 'senior', 'admin')",
            name="ck_users_role_level",
        ),
        CheckConstraint(
            "length(domain_function) BETWEEN 3 AND 64",
            name="ck_users_domain_function_length",
        ),
        CheckConstraint(
            "(provisional_token_hash IS NULL AND provisional_expires_at IS NULL) "
            "OR (provisional_token_hash IS NOT NULL "
            "AND provisional_expires_at IS NOT NULL)",
            name="ck_users_provisional_pair",
        ),
        CheckConstraint(
            "provisional_token_hash IS NOT NULL "
            "OR provisional_exchange_key_hash IS NULL",
            name="ck_users_provisional_exchange_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    api_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    provisional_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    provisional_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provisional_exchange_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Para onde a proxima troca deve gravar. NULL preserva o fluxo de hoje
    # (grava em api_token_hash); um nome grava em UserCredentialRow.
    provisional_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role_level: Mapped[str] = mapped_column(String(16), nullable=False)
    domain_function: Mapped[str] = mapped_column(String(64), nullable=False)
    # Areas adicionais concedidas pelo admin; a primaria continua acima.
    extra_domains: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    # Nome completo do LDAP, so exibicao. Nunca participa de autorizacao.
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )


class BootstrapStateRow(Base):
    """Latch persistente: o bootstrap não reabre após o primeiro admin."""

    __tablename__ = "bootstrap_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ServiceCredentialRow(Base):
    """Credencial técnica com escopo mínimo, armazenada somente como HMAC."""

    __tablename__ = "service_credentials"
    __table_args__ = (
        UniqueConstraint("name", "scope", name="uq_service_credentials_name_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class UserCredentialRow(Base):
    """Credencial permanente adicional, isolada por escopo, por usuario.

    Existe para que uma identidade gerida pelo jump server (escopo "jump",
    reemitido a cada login SSH) nunca derrube uma credencial pessoal usada
    fora dele (escopo "personal") -- os dois nunca compartilham a mesma linha.
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "scope", name="uq_user_credentials_user_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    api_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_job_owner_name"),
        UniqueConstraint("supersedes_job_id", name="uq_jobs_supersedes_job_id"),
        UniqueConstraint(
            "root_job_id", "revision_number", name="uq_jobs_root_revision"
        ),
        CheckConstraint(
            "(root_job_id IS NULL AND supersedes_job_id IS NULL "
            "AND revision_number = 1) OR "
            "(root_job_id IS NOT NULL AND supersedes_job_id IS NOT NULL "
            "AND revision_number >= 2)",
            name="ck_jobs_revision_lineage",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # RESTRICT, e nao CASCADE: apagar um usuario nao pode apagar o que ele
    # documentou. Um runbook publicado e conhecimento da equipe, nao
    # propriedade de quem o escreveu -- desligar alguem nao pode desfazer os
    # procedimentos que a equipe passou a seguir. O caminho normal de saida ja
    # e `is_active = false`, que preserva tudo; quem insistir em apagar a linha
    # do usuario no banco agora recebe uma recusa do PostgreSQL em vez de um
    # apagamento silencioso.
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(
        String(280), default="", server_default="", nullable=False
    )
    # NULL significa "o dominio do autor", resolvido na publicacao.
    domain_function: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=JobStatus.PENDING.value, index=True, nullable=False
    )
    commands: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    command_outputs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    runbook_suggestions: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    inferred_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publication_identity: Mapped[dict[str, str | None] | None] = mapped_column(
        JSON, nullable=True
    )
    root_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    supersedes_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    revision_number: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    upload_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    processing_error: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


class UploadQueueRow(Base):
    """Payload cifrado e lease durável consumido por workers concorrentes."""

    __tablename__ = "upload_queue"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    skip_enrichment: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class PublishedDocumentRow(Base):
    """O Markdown publicado, íntegro, do jeito que foi para o repositório.

    Guardar o documento inteiro parece redundante com o Git -- e é, de
    propósito. A redundância é o ponto: com ela o Hub reconstrói a árvore
    publicada sozinho, e trocar de hospedagem deixa de depender de migrar
    repositório.
    """

    __tablename__ = "published_documents"

    job_id: Mapped[str] = mapped_column(
        # RESTRICT pelo mesmo motivo de `jobs.owner_id`: o espelho existe para
        # sobreviver, não para sumir junto. Um job PUBLISHED já é indelével
        # (`delete_job` recusa), então esta trava só cobre remoção manual.
        ForeignKey("jobs.id", ondelete="RESTRICT"), primary_key=True
    )
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    # Relativo à raiz dos documentos, sem o prefixo do provedor Git.
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class PublishedAssetRow(Base):
    """Os bytes da imagem, não uma referência a ela.

    `BYTEA` e não base64: base64 inflaria 33% e cobraria uma decodificação a
    cada leitura, sem nada em troca -- o PostgreSQL já comprime e desloca
    valor grande para TOAST por conta própria.
    """

    __tablename__ = "published_assets"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("published_documents.job_id", ondelete="CASCADE"),
        primary_key=True,
    )
    filename: Mapped[str] = mapped_column(String(128), primary_key=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


def _identity_from_payload(
    payload: dict[str, str | None] | None,
) -> PublicationIdentity | None:
    """Reconstroi a identidade congelada, recusando payload incompleto.

    Username, papel e dominio nunca sao opcionais: sem eles o frontmatter
    sairia com autor vazio ou o artefato iria para o diretorio errado. Uma
    linha corrompida precisa falhar aqui, e nao produzir uma publicacao
    silenciosamente errada. Apenas `display_name` pode faltar -- publicacoes
    anteriores a essa coluna nao tem a chave.
    """

    if payload is None:
        return None
    username = payload.get("username")
    role_level = payload.get("role_level")
    domain_function = payload.get("domain_function")
    if username is None or role_level is None or domain_function is None:
        raise ConflictError("identidade de publicação incompleta na reserva")
    return PublicationIdentity(
        username=username,
        role_level=RoleLevel(role_level),
        domain_function=domain_function,
        display_name=payload.get("display_name"),
    )


def _identity_payload(identity: PublicationIdentity) -> dict[str, str | None]:
    return {
        "username": identity.username,
        "role_level": identity.role_level.value,
        "domain_function": identity.domain_function,
        # Sem persistir aqui, a identidade volta do banco sem o nome e o
        # frontmatter cai para o username -- o artefato e montado a partir
        # da linha reservada, nao do contexto da requisicao.
        "display_name": identity.display_name,
    }


def _sha256_texto(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


_BOOTSTRAP_STATE_ID = "initial-admin"


class SQLAlchemyJobRepository(JobRepository, IdentityRepository, PublishedMirror):
    """Adapter assíncrono; toda busca de Job exige também o owner_id."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url, pool_pre_ping=True
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        # SQLite ignora SELECT FOR UPDATE nos testes. O lock local complementa a
        # linha bloqueada no PostgreSQL sem ser a garantia de produção.
        self._bootstrap_lock = asyncio.Lock()
        # SQLite ignora FOR UPDATE. Este lock existe apenas no adapter de testes;
        # PostgreSQL continua concorrente e usa o lock de linha abaixo.
        self._revision_lock = asyncio.Lock()
        # SQLite não oferece o mesmo bloqueio de linha do PostgreSQL. Este lock
        # mantém os testes e instalações locais de worker único com uso único.
        self._token_exchange_lock = asyncio.Lock()
        self._upload_claim_lock = asyncio.Lock()
        # Revogacao e rebaixamento disputam o mesmo invariante e por isso
        # compartilham um unico lock: dois locks separados deixariam passar a
        # combinacao "revoga um admin enquanto rebaixa o outro".
        self._admin_lock = asyncio.Lock()
        self._is_sqlite = self._engine.dialect.name == "sqlite"

    async def initialize(self) -> None:
        if not self._is_sqlite:
            # Antes do create_all: migrações como a 005 criam tabela que o
            # modelo também descreve. Deixar o modelo criar primeiro faria a
            # migração falhar num banco antigo, com CREATE TABLE sobre a que
            # acabou de nascer.
            await aplicar_migracoes(self._engine)
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        if not self._is_sqlite:
            await registrar_esquema_do_modelo(self._engine)
        await self._ensure_bootstrap_state()

    async def close(self) -> None:
        await self._engine.dispose()

    async def rotate_service_credential(
        self, name: str, scope: str, token_hash: str
    ) -> None:
        try:
            async with self._sessions() as session, session.begin():
                row = await session.scalar(
                    select(ServiceCredentialRow)
                    .where(
                        ServiceCredentialRow.name == name,
                        ServiceCredentialRow.scope == scope,
                    )
                    .with_for_update()
                )
                if row is None:
                    session.add(
                        ServiceCredentialRow(
                            id=str(uuid4()),
                            name=name,
                            scope=scope,
                            token_hash=token_hash,
                            is_active=True,
                        )
                    )
                else:
                    row.token_hash = token_hash
                    row.is_active = True
                    row.updated_at = datetime.now(timezone.utc)
                await session.flush()
        except IntegrityError as error:
            raise ConflictError("não foi possível rotacionar a credencial técnica") from error

    async def has_service_credential(self, token_hash: str, scope: str) -> bool:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ServiceCredentialRow.id).where(
                    ServiceCredentialRow.token_hash == token_hash,
                    ServiceCredentialRow.scope == scope,
                    ServiceCredentialRow.is_active.is_(True),
                )
            )
        return row is not None

    async def _ensure_bootstrap_state(self) -> None:
        """Inicializa o latch e preserva bancos que já possuam algum admin."""

        try:
            async with self._sessions() as session, session.begin():
                state = await session.get(BootstrapStateRow, _BOOTSTRAP_STATE_ID)
                if state is not None:
                    return
                admin_count = await session.scalar(
                    select(func.count())
                    .select_from(UserRow)
                    .where(UserRow.role_level == RoleLevel.ADMIN.value)
                )
                session.add(
                    BootstrapStateRow(
                        id=_BOOTSTRAP_STATE_ID,
                        completed=bool(admin_count),
                    )
                )
        except IntegrityError:
            # Outro processo inicializou a mesma linha primeiro.
            return

    async def create_bootstrap_admin(
        self,
        username: str,
        api_token_hash: str,
        domain_function: str,
    ) -> User:
        row = UserRow(
            id=str(uuid4()),
            username=username,
            api_token_hash=api_token_hash,
            role_level=RoleLevel.ADMIN.value,
            domain_function=domain_function,
            is_active=True,
        )
        try:
            async with self._bootstrap_lock:
                async with self._sessions() as session, session.begin():
                    state = await session.get(
                        BootstrapStateRow,
                        _BOOTSTRAP_STATE_ID,
                        with_for_update=True,
                    )
                    if state is None:
                        raise RuntimeError("estado de bootstrap não inicializado")
                    if state.completed:
                        raise ConflictError(
                            "já existe ou já existiu um admin; use /admin/users"
                        )
                    session.add(row)
                    state.completed = True
                    await session.flush()
        except IntegrityError as error:
            raise ConflictError("usuário já existe") from error
        return self._to_user(row)

    async def create_user(
        self,
        username: str,
        api_token_hash: str,
        role_level: RoleLevel,
        domain_function: str,
    ) -> User:
        row = UserRow(
            id=str(uuid4()),
            username=username,
            api_token_hash=api_token_hash,
            role_level=role_level.value,
            domain_function=domain_function,
            is_active=True,
        )
        try:
            async with self._sessions() as session:
                session.add(row)
                await session.commit()
        except IntegrityError as error:
            raise ConflictError("usuário já existe") from error
        return self._to_user(row)

    async def create_provisioned_user(
        self,
        username: str,
        provisional_token_hash: str,
        provisional_expires_at: datetime,
        role_level: RoleLevel,
        domain_function: str,
        extra_domains: tuple[str, ...] = (),
        display_name: str | None = None,
        scope: str | None = None,
    ) -> User:
        row = UserRow(
            id=str(uuid4()),
            username=username,
            api_token_hash=None,
            provisional_token_hash=provisional_token_hash,
            provisional_expires_at=provisional_expires_at,
            provisional_scope=scope,
            role_level=role_level.value,
            domain_function=domain_function,
            extra_domains=[
                dominio for dominio in extra_domains if dominio != domain_function
            ],
            display_name=display_name,
            is_active=True,
        )
        try:
            async with self._sessions() as session:
                session.add(row)
                await session.commit()
        except IntegrityError as error:
            raise ConflictError("usuário já existe") from error
        return self._to_user(row)

    async def find_user_by_token_hash(self, api_token_hash: str) -> User | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(UserRow).where(UserRow.api_token_hash == api_token_hash)
            )
            if row is not None:
                return self._to_user(row)
            # Nao achou na coluna legada -- tenta credencial com escopo.
            # UserRow.is_active entra na propria condicao (nao so no filtro
            # de UserCredentialRow): revogar a identidade tem que invalidar
            # toda credencial dela, mesmo que o cascade de revoke_user falhe
            # ou seja esquecido em algum caminho futuro.
            credencial = await session.scalar(
                select(UserRow)
                .join(UserCredentialRow, UserCredentialRow.user_id == UserRow.id)
                .where(
                    UserCredentialRow.api_token_hash == api_token_hash,
                    UserCredentialRow.is_active.is_(True),
                    UserRow.is_active.is_(True),
                )
            )
        return None if credencial is None else self._to_user(credencial)

    async def has_user_credential(self, user_id: str, scope: str) -> bool:
        async with self._sessions() as session:
            existente = await session.scalar(
                select(UserCredentialRow.id).where(
                    UserCredentialRow.user_id == user_id,
                    UserCredentialRow.scope == scope,
                    UserCredentialRow.is_active.is_(True),
                )
            )
        return existente is not None

    async def issue_permanent_credential(
        self, user_id: str, scope: str, api_token_hash: str
    ) -> None:
        try:
            async with self._sessions() as session:
                session.add(
                    UserCredentialRow(
                        id=str(uuid4()),
                        user_id=user_id,
                        scope=scope,
                        api_token_hash=api_token_hash,
                        is_active=True,
                    )
                )
                await session.commit()
        except IntegrityError as error:
            raise ConflictError(
                "credential already exists for this user and scope"
            ) from error

    async def get_user(self, user_id: str) -> User:
        async with self._sessions() as session:
            row = await session.get(UserRow, user_id)
        if row is None:
            raise NotFoundError("usuário não encontrado")
        return self._to_user(row)

    async def get_user_by_identifier(self, id_or_username: str) -> User:
        async with self._sessions() as session:
            row = await session.scalar(
                select(UserRow).where(
                    or_(
                        UserRow.id == id_or_username,
                        UserRow.username == id_or_username,
                    )
                )
            )
        if row is None:
            raise NotFoundError("usuário não encontrado")
        return self._to_user(row)

    async def issue_provisional_token(
        self,
        user_id: str,
        provisional_token_hash: str,
        provisional_expires_at: datetime,
        display_name: str | None = None,
        scope: str | None = None,
    ) -> User:
        try:
            async with self._sessions() as session, session.begin():
                row = await session.get(UserRow, user_id, with_for_update=True)
                if row is None:
                    raise NotFoundError("usuário não encontrado")
                if not row.is_active:
                    raise ConflictError(
                        "usuário revogado não pode receber token provisório"
                    )
                if display_name is not None:
                    # O enrollment roda a cada login no jump, entao uma troca de
                    # nome no LDAP chega sozinha. `None` preserva o atual.
                    row.display_name = display_name
                if scope is None:
                    # A emissão é também uma revogação imediata do token perdido --
                    # só da coluna legada, que é a que este escopo usa.
                    row.api_token_hash = None
                else:
                    # Mesma revogação imediata, mas isolada no escopo pedido: nao
                    # pode tocar a credencial legada nem a de outro escopo.
                    await session.execute(
                        update(UserCredentialRow)
                        .where(
                            UserCredentialRow.user_id == user_id,
                            UserCredentialRow.scope == scope,
                        )
                        .values(is_active=False)
                    )
                row.provisional_token_hash = provisional_token_hash
                row.provisional_expires_at = provisional_expires_at
                row.provisional_exchange_key_hash = None
                row.provisional_scope = scope
                await session.flush()
        except IntegrityError as error:
            # Colisão criptográfica é improvável, mas o contrato deve permanecer seguro.
            raise ConflictError("não foi possível emitir um token exclusivo") from error
        return self._to_user(row)

    async def exchange_provisional_token(
        self,
        provisional_token_hash: str,
        api_token_hash: str,
        idempotency_key_hash: str,
        exchanged_at: datetime,
    ) -> User:
        async def exchange() -> User:
            expired = False
            async with self._sessions() as session, session.begin():
                row = await session.scalar(
                    select(UserRow)
                    .where(
                        UserRow.provisional_token_hash == provisional_token_hash
                    )
                    .with_for_update()
                )
                if row is None or not row.is_active:
                    raise AuthenticationError("invalid provisional token")
                expires_at = row.provisional_expires_at
                if expires_at is None:
                    raise AuthenticationError("invalid provisional token")
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= exchanged_at:
                    # A limpeza é confirmada antes do erro para impedir acúmulo e
                    # garantir que um token expirado permaneça definitivamente inútil.
                    row.provisional_token_hash = None
                    row.provisional_expires_at = None
                    row.provisional_exchange_key_hash = None
                    expired = True
                elif row.provisional_exchange_key_hash is not None:
                    if row.provisional_exchange_key_hash != idempotency_key_hash:
                        raise AuthenticationError("token provisório já utilizado")
                    if row.provisional_scope is None:
                        confere = row.api_token_hash == api_token_hash
                    else:
                        hash_existente = await session.scalar(
                            select(UserCredentialRow.api_token_hash).where(
                                UserCredentialRow.user_id == row.id,
                                UserCredentialRow.scope == row.provisional_scope,
                            )
                        )
                        confere = hash_existente == api_token_hash
                    if not confere:
                        raise AuthenticationError("token provisório já utilizado")
                elif row.provisional_scope is None:
                    # Comportamento de sempre: grava na coluna legada.
                    row.api_token_hash = api_token_hash
                    row.provisional_exchange_key_hash = idempotency_key_hash
                else:
                    # Credencial isolada por escopo -- nunca toca a coluna legada
                    # nem a de outro escopo. Reemissao (issue_provisional_token)
                    # ja desativou a linha anterior deste escopo, entao ou ela
                    # existe desativada (reativa e atualiza) ou nunca existiu
                    # (cria); a UNIQUE(user_id, scope) proibe as duas ao mesmo tempo.
                    existente = await session.scalar(
                        select(UserCredentialRow).where(
                            UserCredentialRow.user_id == row.id,
                            UserCredentialRow.scope == row.provisional_scope,
                        )
                    )
                    if existente is not None:
                        existente.api_token_hash = api_token_hash
                        existente.is_active = True
                    else:
                        session.add(
                            UserCredentialRow(
                                id=str(uuid4()),
                                user_id=row.id,
                                scope=row.provisional_scope,
                                api_token_hash=api_token_hash,
                                is_active=True,
                            )
                        )
                    row.provisional_exchange_key_hash = idempotency_key_hash
                await session.flush()
            if expired:
                raise AuthenticationError("token provisório expirado")
            return self._to_user(row)

        try:
            if self._is_sqlite:
                async with self._token_exchange_lock:
                    return await exchange()
            return await exchange()
        except IntegrityError as error:
            raise ConflictError("não foi possível emitir um token exclusivo") from error

    async def _lock_active_admins(self, session: AsyncSession) -> set[str]:
        """Trava o conjunto de admins ativos e devolve seus ids.

        Um Hub sem admin ativo não pode ser administrado: ninguém cria usuário,
        concede área ou emite token, e voltar exige o console local da máquina.
        Contar antes de gravar só protege esse invariante se ninguém puder
        mudar o conjunto no intervalo -- daí a trava, dentro da transação que
        grava.
        """
        consulta = (
            select(UserRow.id)
            .where(
                UserRow.role_level == RoleLevel.ADMIN.value,
                UserRow.is_active.is_(True),
            )
            # Ordem fixa: duas transações que travam o mesmo conjunto o fazem na
            # mesma sequência, e uma espera a outra em vez de se enrascarem.
            .order_by(UserRow.id)
        )
        if not self._is_sqlite:
            consulta = consulta.with_for_update()
        return set(await session.scalars(consulta))

    async def _guard_admin_invariant(self, operacao):
        # SQLite ignora FOR UPDATE; o lock local sustenta os testes e as
        # instalações de worker único. PostgreSQL usa a trava de linha acima.
        if self._is_sqlite:
            async with self._admin_lock:
                return await operacao()
        return await operacao()

    async def update_user_scopes(
        self,
        user_id: str,
        role_level: RoleLevel | None,
        domain_function: str | None,
        extra_domains: tuple[str, ...] | None = None,
    ) -> User:
        return await self._guard_admin_invariant(
            lambda: self._update_user_scopes(
                user_id, role_level, domain_function, extra_domains
            )
        )

    async def _update_user_scopes(
        self,
        user_id: str,
        role_level: RoleLevel | None,
        domain_function: str | None,
        extra_domains: tuple[str, ...] | None,
    ) -> User:
        async with self._sessions() as session, session.begin():
            # O conjunto de admins vem antes da linha alvo. Na ordem inversa,
            # duas operações cruzadas -- cada uma travando primeiro o próprio
            # alvo -- ficariam esperando a linha uma da outra.
            admins = await self._lock_active_admins(session)
            row = await session.get(UserRow, user_id, with_for_update=True)
            if row is None:
                raise NotFoundError("usuário não encontrado")
            if not row.is_active:
                raise ConflictError("usuário revogado não pode ser alterado")
            if (
                role_level is not None
                and role_level is not RoleLevel.ADMIN
                and user_id in admins
                and len(admins) <= 1
            ):
                raise ConflictError("o último admin ativo não pode ser rebaixado")
            if role_level is not None:
                row.role_level = role_level.value
            if domain_function is not None:
                row.domain_function = domain_function
            if extra_domains is not None:
                # Lista completa, nao incremento: o admin ve e reescreve o
                # conjunto inteiro, entao revogar uma area e omiti-la.
                row.extra_domains = [
                    dominio
                    for dominio in extra_domains
                    if dominio != row.domain_function
                ]
        return self._to_user(row)

    async def revoke_user(self, user_id: str) -> None:
        await self._guard_admin_invariant(lambda: self._revoke_user(user_id))

    async def _revoke_user(self, user_id: str) -> None:
        async with self._sessions() as session, session.begin():
            admins = await self._lock_active_admins(session)
            row = await session.get(UserRow, user_id, with_for_update=True)
            if row is None:
                raise NotFoundError("usuário não encontrado")
            if user_id in admins and len(admins) <= 1:
                raise ConflictError("o último admin ativo não pode ser revogado")
            row.is_active = False
            row.api_token_hash = None
            row.provisional_token_hash = None
            row.provisional_expires_at = None
            row.provisional_exchange_key_hash = None
            row.provisional_scope = None
            # A revogacao vale para toda credencial permanente da identidade,
            # nao so a coluna legada -- um escopo pessoal esquecido nao pode
            # sobreviver a revogacao do usuario.
            await session.execute(
                update(UserCredentialRow)
                .where(UserCredentialRow.user_id == user_id)
                .values(is_active=False)
            )

    async def reinstate_user(
        self, user_id: str, provisional_hash: str, expires_at: datetime
    ) -> User:
        """Reativa e emite o provisório na mesma transação.

        Recusa quem já está ativo: seria um comando que parece inofensivo e na
        verdade rotaciona a credencial de alguém trabalhando, derrubando a
        sessão dele sem que ninguém tivesse pedido isso.

        As linhas de `user_credentials` continuam inativas. Reativá-las
        ressuscitaria exatamente os tokens que a revogação matou -- e revogar
        por vazamento é o caso principal do comando.
        """

        async with self._sessions() as session, session.begin():
            row = await session.get(UserRow, user_id, with_for_update=True)
            if row is None:
                raise NotFoundError("usuário não encontrado")
            if row.is_active:
                raise ConflictError(
                    "usuário ativo não precisa ser readmitido; "
                    "use issue-provisional-token para trocar a credencial"
                )
            row.is_active = True
            row.provisional_token_hash = provisional_hash
            row.provisional_expires_at = expires_at
            row.provisional_exchange_key_hash = None
            # Sem escopo: a troca grava na coluna legada, como no primeiro
            # acesso de qualquer identidade criada pelo admin.
            row.provisional_scope = None
            await session.flush()
            return self._to_user(row)

    async def ping(self) -> None:
        """Prova que o banco responde. Erro sobe para quem perguntou."""
        async with self._sessions() as session:
            await session.execute(select(1))

    async def operational_counters(self) -> dict[str, float]:
        """Números que dizem se o Hub está dando conta do recado.

        Fila e idade do item mais antigo são o que revela worker parado: a
        contagem sozinha não distingue "cinco chegaram agora" de "cinco
        presos há quarenta minutos".
        """
        async with self._sessions() as session:
            por_status = await session.execute(
                select(JobRow.status, func.count()).group_by(JobRow.status)
            )
            contadores: dict[str, float] = {
                f"jobs_{status.lower()}": float(total)
                for status, total in por_status.all()
            }
            for estado in JobStatus:
                contadores.setdefault(f"jobs_{estado.value.lower()}", 0.0)

            fila = await session.scalar(
                select(func.count()).select_from(UploadQueueRow)
            )
            contadores["upload_queue_profundidade"] = float(fila or 0)

            mais_antigo = await session.scalar(
                select(func.min(UploadQueueRow.available_at))
            )
        idade = 0.0
        if mais_antigo is not None:
            if mais_antigo.tzinfo is None:
                mais_antigo = mais_antigo.replace(tzinfo=timezone.utc)
            idade = max(
                0.0, (datetime.now(timezone.utc) - mais_antigo).total_seconds()
            )
        contadores["upload_queue_idade_maxima_segundos"] = idade
        return contadores

    async def count_active_admins(self) -> int:
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(UserRow)
                .where(
                    UserRow.role_level == RoleLevel.ADMIN.value,
                    UserRow.is_active.is_(True),
                )
            )
        return int(count or 0)

    async def create_job(
        self,
        owner_id: str,
        name: str,
        commands: tuple[str, ...],
        inferred_tags: tuple[str, ...],
    ) -> Job:
        row = JobRow(
            id=str(uuid4()),
            owner_id=owner_id,
            name=name,
            commands=list(commands),
            command_outputs=["" for _ in commands],
            runbook_suggestions=_suggestions_payload(
                RunbookSuggestions("", (), tuple("" for _ in commands), ())
            ),
            inferred_tags=list(inferred_tags),
        )
        try:
            async with self._sessions() as session:
                session.add(row)
                await session.commit()
        except IntegrityError as error:
            raise ConflictError("já existe um Job com esse nome") from error
        return self._to_domain(row)

    async def enqueue_job(
        self,
        owner_id: str,
        name: str,
        fingerprint: str,
        ciphertext: str,
        skip_enrichment: bool = False,
        domain_function: str | None = None,
    ) -> Job:
        row = JobRow(
            id=str(uuid4()),
            owner_id=owner_id,
            name=name,
            domain_function=domain_function,
            status=JobStatus.PROCESSING.value,
            commands=[],
            command_outputs=[],
            runbook_suggestions={},
            inferred_tags=[],
            upload_fingerprint=fingerprint,
        )
        queue_row = UploadQueueRow(
            job_id=row.id,
            ciphertext=ciphertext,
            available_at=datetime.now(timezone.utc),
            skip_enrichment=skip_enrichment,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(row)
                session.add(queue_row)
                await session.flush()
        except IntegrityError:
            async with self._sessions() as session:
                existing = await session.scalar(
                    select(JobRow).where(
                        JobRow.owner_id == owner_id,
                        JobRow.name == name,
                        JobRow.root_job_id.is_(None),
                    )
                )
            if (
                existing is None
                or existing.upload_fingerprint != fingerprint
                or existing.domain_function != domain_function
            ):
                raise ConflictError("já existe um Job com esse nome")
            return self._to_domain(existing)
        return self._to_domain(row)

    async def claim_next_upload(
        self, now: datetime, lease_until: datetime
    ) -> QueuedUpload | None:
        async def claim() -> QueuedUpload | None:
            async with self._sessions() as session, session.begin():
                statement = (
                    select(UploadQueueRow, JobRow.owner_id, JobRow.name)
                    .join(JobRow, JobRow.id == UploadQueueRow.job_id)
                    .where(
                        JobRow.status == JobStatus.PROCESSING.value,
                        UploadQueueRow.available_at <= now,
                        or_(
                            UploadQueueRow.lease_until.is_(None),
                            UploadQueueRow.lease_until < now,
                        ),
                    )
                    .order_by(UploadQueueRow.available_at.asc())
                    .limit(1)
                )
                if not self._is_sqlite:
                    statement = statement.with_for_update(skip_locked=True)
                result = (await session.execute(statement)).first()
                if result is None:
                    return None
                row, owner_id, name = result
                row.attempts += 1
                row.lease_until = lease_until
                await session.flush()
                return QueuedUpload(
                    job_id=row.job_id,
                    owner_id=owner_id,
                    name=name,
                    ciphertext=row.ciphertext,
                    attempts=row.attempts,
                    skip_enrichment=bool(row.skip_enrichment),
                )

        if self._is_sqlite:
            async with self._upload_claim_lock:
                return await claim()
        return await claim()

    async def complete_upload(
        self,
        job_id: str,
        commands: tuple[str, ...],
        command_outputs: tuple[str, ...],
        runbook_suggestions: RunbookSuggestions,
        inferred_tags: tuple[str, ...],
        description: str = "",
    ) -> Job:
        if len(command_outputs) != len(commands):
            raise ValueError("command_outputs deve corresponder aos comandos")
        async with self._sessions() as session, session.begin():
            row = await session.get(JobRow, job_id, with_for_update=True)
            if row is None:
                raise NotFoundError("Job não encontrado")
            if row.status == JobStatus.PENDING.value:
                return self._to_domain(row)
            if row.status != JobStatus.PROCESSING.value:
                raise ConflictError("Job não está em processamento")
            queue_row = await session.get(
                UploadQueueRow, job_id, with_for_update=True
            )
            if queue_row is None:
                raise ConflictError("payload do Job não foi encontrado")
            row.commands = list(commands)
            row.command_outputs = list(command_outputs)
            row.description = description
            row.runbook_suggestions = _suggestions_payload(runbook_suggestions)
            row.inferred_tags = list(inferred_tags)
            row.status = JobStatus.PENDING.value
            row.processing_error = None
            await session.delete(queue_row)
            await session.flush()
        return self._to_domain(row)

    async def reschedule_upload(
        self, job_id: str, available_at: datetime
    ) -> bool:
        async with self._sessions() as session, session.begin():
            row = await session.get(UploadQueueRow, job_id, with_for_update=True)
            if row is None:
                return False
            row.available_at = available_at
            row.lease_until = None
        return True

    async def fail_upload(self, job_id: str, error_code: str) -> bool:
        async with self._sessions() as session, session.begin():
            row = await session.get(JobRow, job_id, with_for_update=True)
            if row is None or row.status != JobStatus.PROCESSING.value:
                return False
            row.status = JobStatus.FAILED.value
            row.processing_error = error_code
            queue_row = await session.get(UploadQueueRow, job_id)
            if queue_row is not None:
                queue_row.lease_until = None
        return True

    async def retry_failed_upload(
        self,
        owner_id: str,
        id_or_name: str,
        available_at: datetime,
        skip_enrichment: bool | None = None,
    ) -> Job:
        async with self._sessions() as session, session.begin():
            row = await self._find_row(
                session, owner_id, id_or_name, for_update=True
            )
            if row.status != JobStatus.FAILED.value:
                raise ConflictError("somente Job FAILED pode ser reenfileirado")
            queue_row = await session.get(
                UploadQueueRow, row.id, with_for_update=True
            )
            if queue_row is None:
                raise ConflictError("payload do Job expirou ou foi removido")
            row.status = JobStatus.PROCESSING.value
            row.processing_error = None
            queue_row.attempts = 0
            queue_row.available_at = available_at
            queue_row.lease_until = None
            # None preserva a escolha do upload original; o retry só altera a
            # política quando o operador a informa explicitamente.
            if skip_enrichment is not None:
                queue_row.skip_enrichment = skip_enrichment
            await session.flush()
        return self._to_domain(row)

    async def list_pending(self, owner_id: str) -> list[Job]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(JobRow)
                    .where(
                        JobRow.owner_id == owner_id,
                        JobRow.status == JobStatus.PENDING.value,
                        JobRow.root_job_id.is_(None),
                    )
                    .order_by(JobRow.created_at.asc())
                )
            ).all()
        return [self._to_domain(row) for row in rows]

    async def list_active(self, owner_id: str) -> list[Job]:
        """Retorna somente Jobs que ainda exigem processamento ou ação humana."""

        active_statuses = (
            JobStatus.PROCESSING.value,
            JobStatus.PENDING.value,
            JobStatus.FAILED.value,
        )
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(JobRow)
                    .where(
                        JobRow.owner_id == owner_id,
                        JobRow.status.in_(active_statuses),
                        JobRow.root_job_id.is_(None),
                    )
                    .order_by(JobRow.created_at.asc())
                )
            ).all()
        return [self._to_domain(row) for row in rows]

    async def get_job(self, owner_id: str, id_or_name: str) -> Job:
        async with self._sessions() as session:
            row = await self._find_row(session, owner_id, id_or_name)
        return self._to_domain(row)

    async def save_published(self, document: MirroredDocument) -> None:
        """Grava o espelho da publicação numa transação só.

        Idempotente porque a republicação é: um retry depois de uma queda
        reescreve exatamente o mesmo estado. Os assets são apagados e
        regravados em vez de conciliados um a um -- o conjunto é pequeno (no
        máximo `MAX_ASSETS_PER_PUBLICATION`) e a substituição inteira dispensa
        raciocinar sobre qual imagem saiu entre uma tentativa e outra.
        """

        async with self._sessions() as session, session.begin():
            espelho = await session.get(PublishedDocumentRow, document.job_id)
            if espelho is None:
                session.add(
                    PublishedDocumentRow(
                        job_id=document.job_id,
                        markdown=document.markdown,
                        relative_path=document.relative_path,
                        document_sha256=_sha256_texto(document.markdown),
                        published_at=datetime.now(timezone.utc),
                    )
                )
            else:
                espelho.markdown = document.markdown
                espelho.relative_path = document.relative_path
                espelho.document_sha256 = _sha256_texto(document.markdown)
                anteriores = await session.scalars(
                    select(PublishedAssetRow).where(
                        PublishedAssetRow.job_id == document.job_id
                    )
                )
                for linha in anteriores.all():
                    await session.delete(linha)
                await session.flush()
            for asset in document.assets:
                session.add(
                    PublishedAssetRow(
                        job_id=document.job_id,
                        filename=asset.filename,
                        relative_path=asset.relative_path,
                        content=asset.content,
                        content_sha256=hashlib.sha256(asset.content).hexdigest(),
                    )
                )

    async def published_ids_without_mirror(self) -> tuple[str, ...]:
        """Publicações que o espelho ainda não tem, da mais antiga à mais nova.

        A ordem é a de criação porque uma revisão pode herdar a imagem de um
        ancestral: espelhar o ancestral antes deixa o relatório do backfill
        legível quando alguma leitura falha no meio.
        """

        async with self._sessions() as session:
            espelhados = select(PublishedDocumentRow.job_id)
            identificadores = (
                await session.scalars(
                    select(JobRow.id)
                    .where(
                        JobRow.status == JobStatus.PUBLISHED.value,
                        JobRow.id.not_in(espelhados),
                    )
                    .order_by(JobRow.created_at.asc(), JobRow.id.asc())
                )
            ).all()
        return tuple(identificadores)

    async def iter_published_mirror(self) -> AsyncIterator[MirroredDocument]:
        """Percorre o espelho inteiro, um documento por vez.

        Um documento de cada vez porque os anexos são bytes: carregar a árvore
        toda na memória para escrevê-la em disco não escala com o acervo, e
        quem exporta só precisa de um por vez.
        """

        async with self._sessions() as session:
            identificadores = (
                await session.scalars(
                    select(PublishedDocumentRow.job_id).order_by(
                        PublishedDocumentRow.relative_path.asc()
                    )
                )
            ).all()
            for job_id in identificadores:
                documento = await session.get(PublishedDocumentRow, job_id)
                if documento is None:
                    continue
                anexos = (
                    await session.scalars(
                        select(PublishedAssetRow)
                        .where(PublishedAssetRow.job_id == job_id)
                        .order_by(PublishedAssetRow.filename.asc())
                    )
                ).all()
                yield MirroredDocument(
                    job_id=documento.job_id,
                    markdown=documento.markdown,
                    relative_path=documento.relative_path,
                    assets=tuple(
                        MirroredAsset(
                            filename=anexo.filename,
                            relative_path=anexo.relative_path,
                            content=anexo.content,
                        )
                        for anexo in anexos
                    ),
                )

    async def get_published_for_revision(self, job_id: str) -> RevisionSource:
        async with self._sessions() as session:
            row = await session.scalar(
                select(JobRow).where(
                    JobRow.id == job_id,
                    JobRow.status == JobStatus.PUBLISHED.value,
                )
            )
            if row is None:
                raise NotFoundError("published runbook not found")
            root_id = row.root_job_id or row.id
            root = row if root_id == row.id else await session.scalar(
                select(JobRow).where(
                    JobRow.id == root_id,
                    JobRow.status == JobStatus.PUBLISHED.value,
                )
            )
        if root is None:
            raise ConflictError("publicação raiz da revisão não foi encontrada")
        raiz = self._to_domain(root)
        root_identity = raiz.publication_identity
        if root_identity is None:
            raise ConflictError("publicação raiz não possui identidade confiável")
        return RevisionSource(
            job=self._to_domain(row),
            root_identity=root_identity,
            root_created_at=raiz.created_at,
        )

    async def list_published_runbook_ids(self, max_ids: int) -> tuple[str, ...]:
        async with self._sessions() as session:
            identifiers = (
                await session.scalars(
                    select(JobRow.id)
                    .where(
                        JobRow.status == JobStatus.PUBLISHED.value,
                        JobRow.storage_url.like("local://%"),
                    )
                    .order_by(JobRow.id.asc())
                    .limit(max_ids + 1)
                )
            ).all()
        if len(identifiers) > max_ids:
            raise ConflictError("catálogo publicado excede o limite de 10000 IDs")
        return tuple(identifiers)

    async def list_published_runbooks_for_domains(
        self, allowed_domains: tuple[str, ...] | None, max_ids: int
    ) -> tuple[tuple[str, str], ...]:
        # O dominio confiavel e o congelado em publication_identity na hora da
        # publicacao -- a coluna solta JobRow.domain_function nao e
        # sincronizada nesse momento e frequentemente fica None.
        conditions = [JobRow.status == JobStatus.PUBLISHED.value]
        if allowed_domains is not None:
            conditions.append(
                JobRow.publication_identity["domain_function"].as_string().in_(
                    allowed_domains
                )
            )
        async with self._sessions() as session:
            linhas = (
                await session.execute(
                    select(JobRow.id, JobRow.name)
                    .where(*conditions)
                    .order_by(JobRow.id.asc())
                    .limit(max_ids + 1)
                )
            ).all()
        if len(linhas) > max_ids:
            raise ConflictError("catálogo de revisáveis excede o limite de 10000 IDs")
        return tuple((linha.id, linha.name) for linha in linhas)

    async def reserve_publication(
        self,
        owner_id: str,
        id_or_name: str,
        content_hash: str,
        idempotency_key: str,
        publication_identity: PublicationIdentity,
    ) -> Job:
        async with self._sessions() as session, session.begin():
            row = await self._find_row(
                session, owner_id, id_or_name, for_update=True
            )
            # `lucien start -r` escolheu o diretorio ainda na captura. Username e
            # papel continuam vindo do contexto autenticado -- so o dominio pode
            # ter sido redirecionado, e o Hub ja validou o pedido no upload.
            if row.domain_function is not None:
                # `replace` preserva os demais campos. Reconstruir o objeto
                # campo a campo descartava o nome do LDAP em silencio, e o
                # artefato saia com o username sozinho.
                publication_identity = replace(
                    publication_identity, domain_function=row.domain_function
                )
            same_reservation = (
                row.idempotency_key == idempotency_key
                and row.content_hash == content_hash
            )
            if row.status == JobStatus.PUBLISHED.value:
                if not same_reservation:
                    raise ConflictError(
                        "Job publicado é imutável; conteúdo ou chave divergem da publicação"
                    )
            elif row.status != JobStatus.PENDING.value:
                raise ConflictError("Job ainda não está pronto para publicação")
            elif (
                row.idempotency_key == idempotency_key
                and row.content_hash != content_hash
            ):
                raise ConflictError(
                    "Idempotency-Key já foi usada com conteúdo diferente"
                )
            elif not same_reservation:
                # Enquanto PENDING, uma nova tentativa substitui a reserva anterior;
                # conteúdo editado exige uma nova Idempotency-Key.
                row.idempotency_key = idempotency_key
                row.content_hash = content_hash
                row.publication_identity = _identity_payload(publication_identity)
            if row.publication_identity is None:
                row.publication_identity = _identity_payload(publication_identity)
        return self._to_domain(row)

    async def mark_published(
        self,
        owner_id: str,
        job_id: str,
        storage_url: str,
        content_hash: str,
        idempotency_key: str,
    ) -> Job:
        async with self._sessions() as session, session.begin():
            row = await self._find_row(session, owner_id, job_id, for_update=True)
            if row.content_hash != content_hash or row.idempotency_key != idempotency_key:
                raise ConflictError("reserva de publicação divergente")
            row.status = JobStatus.PUBLISHED.value
            row.storage_url = storage_url
        return self._to_domain(row)

    async def reserve_revision(
        self,
        owner_id: str,
        source_job_id: str,
        expected_content_hash: str,
        content_hash: str,
        idempotency_key: str,
        publication_identity: PublicationIdentity,
        commands: tuple[str, ...],
        stale_before: datetime,
    ) -> Job:
        if self._is_sqlite:
            async with self._revision_lock:
                return await self._reserve_revision_transaction(
                    owner_id,
                    source_job_id,
                    expected_content_hash,
                    content_hash,
                    idempotency_key,
                    publication_identity,
                    commands,
                    stale_before,
                )
        return await self._reserve_revision_transaction(
            owner_id,
            source_job_id,
            expected_content_hash,
            content_hash,
            idempotency_key,
            publication_identity,
            commands,
            stale_before,
        )

    async def _conflito_de_versao_superada(
        self, session: AsyncSession, source: JobRow
    ) -> ConflictError:
        """Recusa que diz qual versão revisar, e não só que esta não serve.

        Dizer apenas "já possui revisão" deixa quem errou uma vez descobrindo
        sozinho. E o sucessor imediato pode nem ser a resposta: a linhagem
        cresce, e o que se revisa é sempre a ponta dela.
        """
        atual = await session.scalar(
            select(JobRow.id)
            .where(
                JobRow.root_job_id == (source.root_job_id or source.id),
                JobRow.status == JobStatus.PUBLISHED.value,
            )
            .order_by(JobRow.revision_number.desc())
            .limit(1)
        )
        if atual is not None and atual != source.id:
            return ConflictError(
                "esta versão já foi revisada; revise sempre a mais recente da "
                f"linhagem: {atual}"
            )
        return ConflictError(
            "esta versão já foi revisada; recarregue o runbook e revise a "
            "versão mais recente"
        )

    async def _reserve_revision_transaction(
        self,
        owner_id: str,
        source_job_id: str,
        expected_content_hash: str,
        content_hash: str,
        idempotency_key: str,
        publication_identity: PublicationIdentity,
        commands: tuple[str, ...],
        stale_before: datetime,
    ) -> Job:
        async with self._sessions() as session, session.begin():
            source = await session.scalar(
                select(JobRow)
                .where(
                    JobRow.id == source_job_id,
                    JobRow.status == JobStatus.PUBLISHED.value,
                )
                .with_for_update()
            )
            if source is None:
                raise NotFoundError("published runbook not found")
            if source.content_hash != expected_content_hash:
                raise PreconditionFailedError(
                    "versão base divergente; recarregue o runbook"
                )

            successor = await session.scalar(
                select(JobRow)
                .where(JobRow.supersedes_job_id == source.id)
                .with_for_update()
            )
            if successor is not None:
                if successor.status == JobStatus.PUBLISHED.value:
                    if successor.content_hash == content_hash:
                        # O sucessor é único por fonte. Aceitar o mesmo conteúdo
                        # torna também idempotente o retry do ator reconciliador.
                        return self._to_domain(successor)
                    raise await self._conflito_de_versao_superada(session, source)
                if (
                    successor.idempotency_key == idempotency_key
                    and successor.content_hash != content_hash
                ):
                    raise ConflictError(
                        "Idempotency-Key já foi usada com conteúdo diferente"
                    )
                if successor.content_hash == content_hash:
                    # Um ator ainda autorizado pode reconciliar a mesma reserva
                    # após expiração do formulário ou revogação do autor original.
                    return self._to_domain(successor)

                created_at = successor.created_at
                if created_at.tzinfo is None:
                    # SQLite devolve DateTime timezone=True sem tzinfo nos testes.
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at > stale_before:
                    raise await self._conflito_de_versao_superada(session, source)

                # Um possível artefato da reserva expirada usa outro UUID e jamais
                # é sobrescrito. Ele pode permanecer órfão no disco, mas o catálogo
                # autenticado expõe apenas IDs PUBLISHED registrados no banco.
                await session.delete(successor)
                await session.flush()

            root_job_id = source.root_job_id or source.id
            revision_number = source.revision_number + 1
            # A raiz, e nao o antecessor: encadear o nome do antecessor daria
            # `...-version-2-version-3` na terceira revisao.
            raiz: JobRow | None = source
            if source.root_job_id is not None:
                raiz = await session.get(JobRow, root_job_id)
            row = JobRow(
                id=str(uuid4()),
                owner_id=owner_id,
                name=revision_artifact_name(
                    raiz.name if raiz is not None else None,
                    revision_number,
                    root_job_id,
                ),
                status=JobStatus.PENDING.value,
                commands=list(commands),
                command_outputs=["" for _ in commands],
                runbook_suggestions={},
                inferred_tags=list(source.inferred_tags),
                created_at=datetime.now(timezone.utc),
                content_hash=content_hash,
                idempotency_key=idempotency_key,
                publication_identity=_identity_payload(publication_identity),
                root_job_id=root_job_id,
                supersedes_job_id=source.id,
                revision_number=revision_number,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as error:
                # O nome legível pode colidir com um Job que o autor tenha
                # criado com esse mesmo nome. Raro, e recuperável renomeando --
                # mas precisa sair como conflito, e não como erro interno.
                raise ConflictError(
                    "já existe um Job com o nome desta revisão; renomeie-o"
                ) from error
        return self._to_domain(row)

    async def mark_revision_published(
        self,
        owner_id: str,
        revision_job_id: str,
        storage_url: str,
        content_hash: str,
        idempotency_key: str,
    ) -> Job:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(JobRow)
                .where(
                    JobRow.id == revision_job_id,
                    JobRow.owner_id == owner_id,
                    JobRow.root_job_id.is_not(None),
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("revisão não encontrada")
            if row.content_hash != content_hash or row.idempotency_key != idempotency_key:
                raise ConflictError("reserva de revisão divergente")
            row.status = JobStatus.PUBLISHED.value
            row.storage_url = storage_url
        return self._to_domain(row)

    async def delete_job(
        self, owner_id: str, id_or_name: str, force: bool = False
    ) -> Job:
        async with self._sessions() as session, session.begin():
            row = await self._find_row(
                session, owner_id, id_or_name, for_update=True
            )
            allowed_statuses = {
                JobStatus.PENDING.value,
                JobStatus.FAILED.value,
            }
            if force:
                allowed_statuses.add(JobStatus.PROCESSING.value)
            if row.status not in allowed_statuses:
                if row.status == JobStatus.PUBLISHED.value:
                    raise ConflictError(
                        "a PUBLISHED job is immutable and cannot be purged"
                    )
                raise ConflictError(
                    "only PENDING or FAILED jobs can be purged; "
                    "use --force to cancel a PROCESSING job"
                )
            deleted = self._to_domain(row)
            queue_row = await session.get(
                UploadQueueRow, row.id, with_for_update=True
            )
            if queue_row is not None:
                await session.delete(queue_row)
            await session.delete(row)
        return deleted

    @staticmethod
    async def _find_row(
        session: object,
        owner_id: str,
        id_or_name: str,
        for_update: bool = False,
    ) -> JobRow:
        statement = select(JobRow).where(
            JobRow.owner_id == owner_id,
            JobRow.root_job_id.is_(None),
            or_(JobRow.id == id_or_name, JobRow.name == id_or_name),
        )
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)  # type: ignore[attr-defined]
        if row is None:
            raise NotFoundError("Job não encontrado")
        return row

    @staticmethod
    def _to_user(row: UserRow) -> User:
        extras = row.extra_domains or []
        return User(
            id=row.id,
            username=row.username,
            role_level=RoleLevel(row.role_level),
            domain_function=row.domain_function,
            is_active=row.is_active,
            display_name=row.display_name,
            # A primaria nunca se repete nas extras: `authorized_domains` ja
            # une as duas, e duplicar poluiria a listagem do admin.
            extra_domains=tuple(
                dominio for dominio in extras if dominio != row.domain_function
            ),
        )

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        identity = row.publication_identity
        return Job(
            id=row.id,
            owner_id=row.owner_id,
            name=row.name,
            status=JobStatus(row.status),
            commands=tuple(row.commands),
            command_outputs=_aligned_command_outputs(row.commands, row.command_outputs),
            runbook_suggestions=_to_suggestions(
                row.commands, row.runbook_suggestions
            ),
            inferred_tags=tuple(row.inferred_tags),
            created_at=row.created_at,
            description=row.description or "",
            domain_function=row.domain_function,
            storage_url=row.storage_url,
            content_hash=row.content_hash,
            idempotency_key=row.idempotency_key,
            publication_identity=_identity_from_payload(identity),
            root_job_id=row.root_job_id,
            supersedes_job_id=row.supersedes_job_id,
            revision_number=row.revision_number,
            processing_error=row.processing_error,
        )


def _aligned_command_outputs(
    commands: list[str], outputs: list[str] | None
) -> tuple[str, ...]:
    """Mantém compatibilidade com Jobs anteriores à coluna de saídas."""

    values = list(outputs or [])[: len(commands)]
    values.extend("" for _ in range(len(commands) - len(values)))
    return tuple(values)


def _suggestions_payload(suggestions: RunbookSuggestions) -> dict[str, object]:
    return {
        "objective": suggestions.objective,
        "architecture_prerequisites": list(suggestions.architecture_prerequisites),
        "command_impacts": list(suggestions.command_impacts),
        "rollback_commands": list(suggestions.rollback_commands),
    }


def _to_suggestions(
    commands: list[str], payload: dict[str, object] | None
) -> RunbookSuggestions:
    data = payload or {}

    def strings(name: str) -> tuple[str, ...]:
        value = data.get(name)
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    impacts = list(strings("command_impacts")[: len(commands)])
    impacts.extend("" for _ in range(len(commands) - len(impacts)))
    objective = data.get("objective")
    return RunbookSuggestions(
        objective=objective if isinstance(objective, str) else "",
        architecture_prerequisites=strings("architecture_prerequisites"),
        command_impacts=tuple(impacts),
        rollback_commands=strings("rollback_commands"),
    )
