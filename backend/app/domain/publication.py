import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from app.domain.models import Job, PublicationIdentity, RoleLevel
from app.domain.ports import ForbiddenError, ValidationError


class Criticality(StrEnum):
    LOW = "baixa"
    MEDIUM = "media"
    HIGH = "alta"


@dataclass(frozen=True, slots=True)
class ValidatedPlaybook:
    body: str
    command_blocks: tuple[str, ...]
    criticality: Criticality


# O CLI usa inglês, mas playbooks já publicados podem seguir o contrato antigo em
# português. Aceitar ambos preserva retrocompatibilidade sem relaxar a estrutura.
_STEP_HEADER = re.compile(r"^### (?:Passo|Step) ([1-9][0-9]*): (.{1,120})$")
_FENCE_OPENING = re.compile(r"^(`{3,})")
_HIGH_RISK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\brm\s+[^\n]*(?:-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|--no-preserve-root)",
        r"\bmkfs(?:\.[a-z0-9]+)?\b",
        r"\bdd\b[^\n]*\bof=/dev/",
        r"\bkubectl\s+delete\b",
        r"\bterraform\s+destroy\b",
        r"\b(?:drop|truncate)\s+(?:database|table)\b",
        r"\b(?:shutdown|reboot|poweroff)\b",
        r"\biptables\s+-(?:F|X)\b",
        # Equipamentos de rede: recarga, apagamento de configuração e remoção de
        # assinante derrubam serviço de forma imediata e frequentemente ampla.
        r"^\s*(?:admin\s+)?(?:reload|reboot)\b",
        r"^\s*reset\s+(?:saved-configuration|board|slot|bgp|ospf)\b",
        r"\bwrite\s+erase\b",
        r"\berase\s+(?:startup-config|nvram:|flash:)",
        r"^\s*format\s+(?:flash|cf|sd)",
        r"\b(?:ont|onu)\s+delete\b",
        r"^\s*(?:no|undo)\s+(?:ont|onu)\b",
        r"^\s*delete\s+(?:ont|onu|subscriber|service)\b",
        r"\bclear\s+cable\s+modem\b[^\n]*\breset\b",
        r"^\s*clear\s+ip\s+bgp\s+\*",
    )
)
_MEDIUM_RISK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\bsudo\b",
        r"\bsystemctl\s+(?:start|stop|restart|reload)\b",
        r"\bkubectl\s+(?:apply|patch|scale)\b",
        r"\bdocker\s+(?:rm|stop|restart)\b",
        # Entrada em modo de configuração e persistência em equipamento de rede:
        # alteram estado corrente ou de boot, mas não derrubam serviço sozinhas.
        r"^\s*(?:configure\s+terminal|config\s+t|system-view|configure\s+private)\b",
        r"^\s*(?:commit|admin\s+save|save|write\s+memory|wr\s+mem)\b",
        r"^\s*copy\s+running-config\s+startup-config\b",
        # `no shutdown` e `undo shutdown` não entram aqui: o padrão de alto risco
        # já casa a palavra `shutdown` e é avaliado primeiro. Restringir mais é
        # seguro; afrouxar aquela regra liberaria publicação hoje bloqueada.
        r"^\s*rollback\b",
    )
)


def validate_playbook(markdown: str) -> ValidatedPlaybook:
    """Valida a gramática de chunking antes de qualquer publicação."""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    without_prefix = normalized.lstrip("\ufeff \t\n")
    if without_prefix == "---" or without_prefix.startswith("---\n"):
        raise ValidationError(
            "client-sent frontmatter is not allowed; the Hub generates the metadata"
        )

    lines = normalized.split("\n")
    command_blocks: list[str] = []
    expected_step = 1
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("### "):
            match = _STEP_HEADER.fullmatch(line)
            if match is None:
                # Subtítulo livre, como o título do objetivo que o CLI escreve
                # a partir de `lucien start -d`. Ele não abre um passo, e por
                # isso não pode preceder um bloco de comando: a checagem de
                # fence abaixo recusa qualquer ```bash órfão, então nenhum
                # comando escapa da gramática de passos por este caminho.
                if index + 1 < len(lines) and lines[index + 1] == "```bash":
                    raise ValidationError(
                        "a bash block must belong to a step heading; "
                        "use '### Step X: Action' or '### Passo X: Ação'"
                    )
                index += 1
                continue
            step_number = int(match.group(1))
            if step_number != expected_step:
                raise ValidationError("steps must be sequential and start at 1")
            if index + 1 >= len(lines) or lines[index + 1] != "```bash":
                raise ValidationError(
                    f"Step {step_number} must be followed immediately by ```bash"
                )

            closing = index + 2
            while closing < len(lines) and lines[closing] != "```":
                closing += 1
            if closing >= len(lines):
                raise ValidationError(f"the bash block of Step {step_number} was not closed")

            command = "\n".join(lines[index + 2 : closing]).strip()
            if not command:
                raise ValidationError(f"Step {step_number} contains no command")
            command_blocks.append(command)
            expected_step += 1
            index = closing + 1
            continue

        fence = _FENCE_OPENING.match(line)
        if fence is not None:
            if line == "```bash":
                raise ValidationError(
                    "a bash block must belong to a step heading"
                )
            # Fence genérico (exemplos, YAML, diagramas): conteúdo é literal e
            # não participa da gramática dos passos.
            index = _skip_generic_fence(lines, index, len(fence.group(1)))
            continue
        index += 1

    if not command_blocks:
        raise ValidationError("the playbook must contain at least one operational step")

    criticality = _classify_criticality(command_blocks)
    return ValidatedPlaybook(
        body=normalized + "\n",
        command_blocks=tuple(command_blocks),
        criticality=criticality,
    )


def _skip_generic_fence(
    lines: list[str], opening_index: int, fence_size: int
) -> int:
    """Avança até depois do fechamento de um fence que não pertence a um passo."""

    index = opening_index + 1
    while index < len(lines):
        closing = _FENCE_OPENING.match(lines[index])
        if (
            closing is not None
            and len(closing.group(1)) >= fence_size
            and lines[index].strip("`") == ""
        ):
            return index + 1
        index += 1
    raise ValidationError("an opened code block was not closed")


def authorize_publication(
    role_level: RoleLevel,
    criticality: Criticality,
    entry_roles_enabled: bool = False,
) -> None:
    """A SLM nunca participa desta decisão de autorização.

    `entry_roles_enabled` reflete RBAC_ENTRY_ROLES_ENABLED e é o único caminho para um
    junior publicar criticidade alta. O padrão mantém o bloqueio.
    """

    if (
        role_level is RoleLevel.JUNIOR
        and criticality is Criticality.HIGH
        and not entry_roles_enabled
    ):
        raise ForbiddenError(
            "a junior user cannot publish a high-criticality operation"
        )


def build_frontmatter(
    job: Job, identity: PublicationIdentity, validated: ValidatedPlaybook
) -> str:
    """Funde metadados confiáveis do servidor com o Markdown já validado."""

    return _build_frontmatter(job, identity, validated, lineage="")


def build_revision_frontmatter(
    job: Job, identity: PublicationIdentity, validated: ValidatedPlaybook
) -> str:
    """Acrescenta a linhagem calculada pelo Hub sem alterar a versão anterior."""

    if (
        job.root_job_id is None
        or job.supersedes_job_id is None
        or job.revision_number < 2
    ):
        raise RuntimeError("Job de revisão sem linhagem confiável")
    lineage = (
        f"runbook_raiz: {json.dumps(job.root_job_id, ensure_ascii=False)}\n"
        f"revisao: {job.revision_number}\n"
        f"substitui: {json.dumps(job.supersedes_job_id, ensure_ascii=False)}\n"
    )
    return _build_frontmatter(job, identity, validated, lineage=lineage)


def _build_frontmatter(
    job: Job,
    identity: PublicationIdentity,
    validated: ValidatedPlaybook,
    lineage: str,
) -> str:
    """Serializa somente valores originados de contexto e persistência confiáveis."""

    created_at = job.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    iso_created_at = created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    tags = list(job.inferred_tags)

    # JSON strings são escalares YAML válidos e impedem quebra/injeção de novas chaves.
    frontmatter = (
        "---\n"
        f"id: {json.dumps(job.id, ensure_ascii=False)}\n"
        f"{lineage}"
        f"autor: {json.dumps(identity.author_label, ensure_ascii=False)}\n"
        f"nivel_autor: {json.dumps(identity.role_level.value, ensure_ascii=False)}\n"
        f"funcao: {json.dumps(identity.domain_function, ensure_ascii=False)}\n"
        f"data_criacao: {json.dumps(iso_created_at, ensure_ascii=False)}\n"
        f"tags_inferidas: {json.dumps(tags, ensure_ascii=False)}\n"
        f"versao: {json.dumps(str(job.revision_number), ensure_ascii=False)}\n"
        'ultimo_revisor: ""\n'
        'data_revisao: ""\n'
        "---\n"
    )
    return f"{frontmatter}{validated.body}"


def classify_criticality(commands: Sequence[str]) -> Criticality:
    """Expõe a mesma tabela de risco da validação para outros consumidores."""

    return _classify_criticality(list(commands))


def _classify_criticality(command_blocks: list[str]) -> Criticality:
    commands = "\n".join(command_blocks)
    if any(pattern.search(commands) for pattern in _HIGH_RISK_PATTERNS):
        return Criticality.HIGH
    if any(pattern.search(commands) for pattern in _MEDIUM_RISK_PATTERNS):
        return Criticality.MEDIUM
    return Criticality.LOW
