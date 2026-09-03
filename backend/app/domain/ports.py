from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.models import (
    Job,
    PublicationIdentity,
    PublishedArtifact,
    QueuedUpload,
    RevisionSource,
    RoleLevel,
    RunbookEnrichment,
    RunbookSuggestions,
    SealedUpload,
    User,
)


class DomainError(Exception):
    """Erro esperado, seguro para conversão em resposta HTTP."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class AuthenticationError(DomainError):
    pass


class PreconditionFailedError(DomainError):
    pass


class ForbiddenError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class UpstreamError(DomainError):
    pass


class SecretDetectedError(ValidationError):
    pass


class IdentityRepository(ABC):
    @abstractmethod
    async def rotate_service_credential(
        self, name: str, scope: str, token_hash: str
    ) -> None:
        """Substitui atomicamente uma credencial M2M de escopo único."""

    @abstractmethod
    async def has_service_credential(self, token_hash: str, scope: str) -> bool:
        """Valida apenas o HMAC e o escopo; o token bruto nunca é persistido."""

    @abstractmethod
    async def create_bootstrap_admin(
        self,
        username: str,
        api_token_hash: str,
        domain_function: str,
    ) -> User:
        """Cria o primeiro admin de forma atômica e fecha o bootstrap."""

    @abstractmethod
    async def create_user(
        self,
        username: str,
        api_token_hash: str,
        role_level: RoleLevel,
        domain_function: str,
    ) -> User: ...

    @abstractmethod
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
        """Cria usuário sem credencial permanente e com ativação temporária.

        `scope=None` preserva o comportamento de sempre: a troca eventual
        grava em `api_token_hash`. Um nome grava a troca em credencial
        isolada por escopo -- é o que o jump server usa (`scope="jump"`),
        pra nunca disputar a mesma coluna que uma credencial pessoal usa.
        """

    @abstractmethod
    async def find_user_by_token_hash(self, api_token_hash: str) -> User | None:
        """Procura primeiro em users.api_token_hash, depois em credencial com escopo."""

    @abstractmethod
    async def has_user_credential(self, user_id: str, scope: str) -> bool:
        """Diz se ja existe credencial permanente ativa naquele escopo."""

    @abstractmethod
    async def issue_permanent_credential(
        self, user_id: str, scope: str, api_token_hash: str
    ) -> None:
        """Cria a credencial permanente de um escopo que ainda nao tem uma.

        Chame só depois de `has_user_credential` confirmar que não existe --
        esta operação não substitui uma credencial já ativa no escopo.
        """

    @abstractmethod
    async def get_user(self, user_id: str) -> User: ...

    @abstractmethod
    async def get_user_by_identifier(self, id_or_username: str) -> User:
        """Localiza uma identidade administrativa por UUID ou username exato."""

    @abstractmethod
    async def issue_provisional_token(
        self,
        user_id: str,
        provisional_token_hash: str,
        provisional_expires_at: datetime,
        display_name: str | None = None,
        scope: str | None = None,
    ) -> User:
        """Invalida a credencial atual e instala uma ativação temporária.

        `scope=None` invalida `api_token_hash` (comportamento de sempre). Um
        nome invalida só a credencial daquele escopo -- reemitir o token do
        jump nunca deve apagar uma credencial pessoal de outro escopo.
        """

    @abstractmethod
    async def exchange_provisional_token(
        self,
        provisional_token_hash: str,
        api_token_hash: str,
        idempotency_key_hash: str,
        exchanged_at: datetime,
    ) -> User:
        """Ativa uma vez e reconcilia retries da mesma operação atomicamente."""

    @abstractmethod
    async def update_user_scopes(
        self,
        user_id: str,
        role_level: RoleLevel | None,
        domain_function: str | None,
        extra_domains: tuple[str, ...] | None = None,
    ) -> User: ...

    @abstractmethod
    async def revoke_user(self, user_id: str) -> None:
        """Desativa a identidade e toda credencial permanente, em qualquer escopo."""

    @abstractmethod
    async def reinstate_user(
        self, user_id: str, provisional_hash: str, expires_at: datetime
    ) -> "User":
        """Reativa uma identidade revogada e já lhe dá uma via de volta.

        Reativar sem emitir credencial deixaria o usuário ativo e sem nenhuma
        forma de entrar -- a revogação apaga todos os hashes, e
        `issue_provisional_token` recusa quem está inativo. As duas coisas
        acontecem na mesma transação para não existir esse estado no meio.

        As credenciais permanentes antigas continuam desativadas de propósito:
        o que foi revogado por vazamento não pode voltar a valer.
        """

    @abstractmethod
    async def count_active_admins(self) -> int: ...


class JobRepository(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def ping(self) -> None:
        """Levanta se o banco não responder. Sustenta a prontidão do Hub."""

    @abstractmethod
    async def operational_counters(self) -> dict[str, float]:
        """Contadores para diagnóstico: jobs por estado e estado da fila."""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def create_job(
        self,
        owner_id: str,
        name: str,
        commands: tuple[str, ...],
        inferred_tags: tuple[str, ...],
    ) -> Job: ...

    @abstractmethod
    async def enqueue_job(
        self,
        owner_id: str,
        name: str,
        fingerprint: str,
        ciphertext: str,
        skip_enrichment: bool = False,
        domain_function: str | None = None,
    ) -> Job:
        """Cria ou reconcilia um upload durável pelo nome e fingerprint."""

    @abstractmethod
    async def claim_next_upload(
        self, now: datetime, lease_until: datetime
    ) -> QueuedUpload | None:
        """Reserva um upload disponível sem bloquear outros workers."""

    @abstractmethod
    async def complete_upload(
        self,
        job_id: str,
        commands: tuple[str, ...],
        command_outputs: tuple[str, ...],
        runbook_suggestions: RunbookSuggestions,
        inferred_tags: tuple[str, ...],
        description: str = "",
    ) -> Job: ...

    @abstractmethod
    async def reschedule_upload(
        self, job_id: str, available_at: datetime
    ) -> bool:
        """Reagenda e informa se o Job ainda existia."""

    @abstractmethod
    async def fail_upload(self, job_id: str, error_code: str) -> bool:
        """Marca falha e informa se o Job ainda existia."""

    @abstractmethod
    async def retry_failed_upload(
        self,
        owner_id: str,
        id_or_name: str,
        available_at: datetime,
        skip_enrichment: bool | None = None,
    ) -> Job: ...

    @abstractmethod
    async def list_pending(self, owner_id: str) -> list[Job]: ...

    @abstractmethod
    async def list_active(self, owner_id: str) -> list[Job]:
        """Lista a fila operacional do proprietário, sem Jobs publicados."""

    @abstractmethod
    async def get_job(self, owner_id: str, id_or_name: str) -> Job: ...

    @abstractmethod
    async def get_published_for_revision(self, job_id: str) -> RevisionSource:
        """Obtém a versão e a identidade imutável da publicação raiz."""

    @abstractmethod
    async def list_published_runbook_ids(self, max_ids: int) -> tuple[str, ...]:
        """Lista IDs publicados que podem integrar o catálogo somente leitura."""

    @abstractmethod
    async def list_published_runbooks_for_domains(
        self, allowed_domains: tuple[str, ...] | None, max_ids: int
    ) -> tuple[tuple[str, str], ...]:
        """Pares (id, nome) publicados que o autor pode revisar de verdade.

        `allowed_domains=None` significa sem filtro (admin); senão, restringe
        pelo dominio congelado em `publication_identity` no momento da
        publicacao -- a coluna solta `domain_function` do Job nao e
        atualizada nesse momento e pode ficar `None`. O nome acompanha o ID
        porque quem revisa precisa reconhecer o runbook sem decorar UUIDs.
        """

    @abstractmethod
    async def reserve_publication(
        self,
        owner_id: str,
        id_or_name: str,
        content_hash: str,
        idempotency_key: str,
        publication_identity: PublicationIdentity,
    ) -> Job: ...

    @abstractmethod
    async def mark_published(
        self,
        owner_id: str,
        job_id: str,
        storage_url: str,
        content_hash: str,
        idempotency_key: str,
    ) -> Job: ...

    @abstractmethod
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
        """Cria ou recupera o único sucessor imutável de uma versão publicada."""

    @abstractmethod
    async def mark_revision_published(
        self,
        owner_id: str,
        revision_job_id: str,
        storage_url: str,
        content_hash: str,
        idempotency_key: str,
    ) -> Job: ...

    @abstractmethod
    async def delete_job(
        self, owner_id: str, id_or_name: str, force: bool = False
    ) -> Job:
        """Expurga Job próprio; force inclui PROCESSING, nunca PUBLISHED."""


class CommandExtractor(ABC):
    @abstractmethod
    async def extract(
        self, sanitized_log: str, sanitized_description: str | None = None
    ) -> tuple[str, ...]: ...


class RunbookEnricher(ABC):
    @abstractmethod
    async def infer(
        self,
        commands: tuple[str, ...],
        sanitized_description: str | None = None,
    ) -> RunbookEnrichment: ...


@dataclass(frozen=True, slots=True)
class SecretScanResult:
    """Veredito do scanner, com a regra que casou quando ela é conhecida.

    `rules` traz apenas o identificador da regra -- `lucien-snmp-community`,
    `generic-api-key` --, nunca o trecho que casou nem o valor. É o que o
    operador precisa para achar o segredo no próprio documento; recusar sem
    dizer o quê obriga a caçar às cegas num Markdown de dezenas de blocos.

    Vazia quando o scanner é antigo e não informa a regra. A recusa continua
    valendo: o veredito é `detected`, e a regra é acréscimo.
    """

    detected: bool
    rules: tuple[str, ...] = ()


class SecretScanner(ABC):
    """Porta independente da DLP para detecção mandatória de segredos."""

    @abstractmethod
    async def detect(self, content: str) -> SecretScanResult: ...


def secret_detection_message(resultado: SecretScanResult) -> str:
    """Diz o que casou, jamais o que foi encontrado.

    A recusa acontece depois de o operador ter escrito o procedimento inteiro
    (ou de colar a imagem). Sem o nome da regra ele procura às cegas -- e a
    tentação, nessa hora, é publicar de outro jeito.

    Somente o identificador da regra atravessa: `lucien-snmp-community` diz que
    houve uma community SNMP, e não qual. O valor fica redigido no scanner, e o
    adaptador só aceita identificadores. Compartilhada entre o gate de texto e
    o de imagem: os dois usam o mesmo SecretScanner por baixo, e a mensagem de
    recusa não pode divergir entre eles.
    """

    base = "content blocked by the secret policy"
    if not resultado.rules:
        return base
    return f"{base} (rule: {', '.join(resultado.rules)})"


@dataclass(frozen=True, slots=True)
class ProcessedAsset:
    """Saida do gate de seguranca de imagem: bytes prontos para publicar.

    Sem nome de arquivo aqui -- quem atribui o nome opaco final e o chamador
    (JobService), depois que o job_id existe.
    """

    content: bytes
    media_type: str


class ImageSecurityScanner(ABC):
    """Porta independente do SecretScanner de texto, mas reutiliza-o por dentro.

    Decodifica de verdade (allowlist PNG/JPEG, nunca confia em extensao ou
    media_type declarado), remove metadado, reencoda, roda OCR e entrega o
    texto extraido para o MESMO SecretScanner de texto -- uma politica de
    segredo so, nao duas.
    """

    @abstractmethod
    async def process(self, raw_bytes: bytes, declared_media_type: str) -> ProcessedAsset:
        """Levanta ValidationError (formato/tamanho/dimensao invalidos) ou
        SecretDetectedError (OCR encontrou segredo) -- nunca vaza o valor
        encontrado, so o identificador da regra, igual ao gate de texto."""


@dataclass(frozen=True, slots=True)
class AssetToPublish:
    """Um anexo ja aprovado pelo gate de seguranca, pronto para o storage."""

    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class RawAssetInput:
    """Um anexo como o cliente enviou, antes de qualquer decodificacao/gate.

    Tipo de dominio, nao o schema Pydantic da API: a camada de aplicacao nunca
    deve depender de `app.api.schemas` (a rota e quem converte um pelo outro).
    """

    filename: str
    content_base64: str
    media_type: str


class UploadCipher(ABC):
    """Cifra o payload transitório e produz fingerprint não reversível."""

    @abstractmethod
    def seal(
        self,
        owner_id: str,
        name: str,
        sanitized_log: str,
        description: str | None,
    ) -> SealedUpload: ...

    @abstractmethod
    def open(
        self, owner_id: str, name: str, ciphertext: str
    ) -> tuple[str, str | None]: ...


@dataclass(frozen=True, slots=True)
class MirroredAsset:
    """Imagem publicada, com o caminho que ela ocupa na árvore da wiki."""

    filename: str
    relative_path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class MirroredDocument:
    """Uma publicação inteira, como o banco a guarda."""

    job_id: str
    markdown: str
    relative_path: str
    assets: tuple[MirroredAsset, ...]


class PublishedMirror(ABC):
    """Cópia em banco de tudo que foi publicado, imagens inclusive.

    O Git é o destino, não o arquivo. Enquanto o conteúdo existisse só lá,
    trocar de hospedagem -- sair do Gitea para uma wiki local, por exemplo --
    dependeria de migrar o repositório, e o Hub não saberia reconstruir nada
    sozinho. Este espelho existe para que a árvore publicada possa ser
    regravada a partir do banco, sem clone e sem provedor.

    O caminho guardado é o relativo à raiz dos documentos, sem o prefixo do
    provedor Git: é o mesmo em `local`, `github` e `gitea`, que é o que
    permite reproduzir a árvore em qualquer um deles.
    """

    @abstractmethod
    async def save_published(self, document: MirroredDocument) -> None:
        """Idempotente: republicar o mesmo conteúdo reescreve o mesmo estado."""


class StorageProvider(ABC):
    """Strategy de destino; publicar o mesmo conteúdo deve ser idempotente."""

    async def aclose(self) -> None:
        """Libera conexões de rede. Destinos em disco não têm o que fechar."""

    @abstractmethod
    async def publish(
        self,
        job_id: str,
        created_at: datetime,
        markdown: str,
        artifact_name: str | None = None,
        domain_function: str | None = None,
        assets: tuple[AssetToPublish, ...] = (),
    ) -> PublishedArtifact: ...

    @abstractmethod
    async def read_published(
        self,
        job_id: str,
        created_at: datetime,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> str:
        """Devolve o Markdown publicado, incluindo o frontmatter gravado.

        O artefato e a fonte de verdade do conteudo: o Hub persiste apenas o
        hash. Sem esta leitura nao ha como abrir uma revisao fora do modo
        local, onde o portal alcanca o volume diretamente.
        """
        ...

    @abstractmethod
    async def read_bytes(self, relative_path: str) -> bytes:
        """Bytes crus de um arquivo publicado, pelo caminho relativo a raiz
        dos documentos (sem o prefixo do provedor Git).

        `read_published` nao serve para isto: ela resolve o caminho sozinha,
        so acha `.md` e devolve texto decodificado. O backfill do espelho
        precisa das duas coisas que faltam ali -- ler um caminho que ja
        conhece, e receber bytes, porque anexo e imagem.
        """
        ...
