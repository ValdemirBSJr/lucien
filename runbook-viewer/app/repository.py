import asyncio
import hashlib
import logging
import os
import re
import stat
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from uuid import UUID

import bleach
import yaml
from markdown_it import MarkdownIt

from app.models import RunbookDocument, RunbookSummary


logger = logging.getLogger("lucien.viewer.catalog")

_YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
_MONTH_PATTERN = re.compile(r"^(0[1-9]|1[0-2])$")
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_NAMED_RUNBOOK_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9._-]{1,128}--)?(?P<id>[0-9a-f-]{36})$"
)
_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
_ROLES = frozenset({"junior", "pleno", "senior", "admin"})
# Conjuntos nomeados, e nao um filtro permissivo: a comparacao exata foi o que
# acusou o Hub publicando campos que este lado nao conhecia. Trocar por
# "aceite o que vier" transformaria a proxima divergencia em falha silenciosa.
#
# O legado continua declarado porque artefatos publicados sao imutaveis: o que
# esta no repositorio com seis chaves permanece la e precisa continuar legivel.
_LEGACY_FRONTMATTER_KEYS = frozenset(
    {"id", "autor", "nivel_autor", "funcao", "data_criacao", "tags_inferidas"}
)
_LEGACY_REVISION_FRONTMATTER_KEYS = _LEGACY_FRONTMATTER_KEYS | frozenset(
    {"runbook_raiz", "revisao", "substitui"}
)
# Campos de revisao humana acrescentados pelo Hub: a versao do artefato e o
# espaco em branco que o revisor preenche.
_CURRENT_FRONTMATTER_KEYS = _LEGACY_FRONTMATTER_KEYS | frozenset(
    {"versao", "ultimo_revisor", "data_revisao"}
)
_REVISION_FRONTMATTER_KEYS = _CURRENT_FRONTMATTER_KEYS | frozenset(
    {"runbook_raiz", "revisao", "substitui"}
)
_ACCEPTED_FRONTMATTER_KEYS = (
    _LEGACY_FRONTMATTER_KEYS,
    _LEGACY_REVISION_FRONTMATTER_KEYS,
    _CURRENT_FRONTMATTER_KEYS,
    _REVISION_FRONTMATTER_KEYS,
)

# O autor e apresentacao e rastreabilidade, nunca fonte de autorizacao. O Hub
# publica `username - Nome Completo`, entao a gramatica de username nao serve:
# ela recusaria espacos e acentos. Aqui basta garantir que o rotulo nao carrega
# controle nem quebra de linha e cabe num cabecalho.
_AUTHOR_MAX_LENGTH = 200


def _valid_author_label(valor: str) -> bool:
    """Rotulo de autor: legivel, de uma linha so e limitado.

    Recusa controle e quebra de linha porque o valor sai num cabecalho e
    numa pagina HTML. Nao tenta validar a forma do nome: `username - Nome`
    e apenas um dos formatos, e o Hub e quem decide qual usar.
    """

    if not 1 <= len(valor) <= _AUTHOR_MAX_LENGTH:
        return False
    return valor == valor.strip() and all(
        caractere.isprintable() for caractere in valor
    )


_ALLOWED_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "del",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_ALLOWED_ATTRIBUTES = {"a": ["href", "title"], "code": ["class"]}
_ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})


class CatalogLimitError(Exception):
    """O volume contém mais documentos do que o limite operacional."""


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader sem aliases e sem chaves duplicadas."""

    def compose_node(self, parent: object, index: object) -> yaml.Node:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases YAML não são permitidos")
        return super().compose_node(parent, index)  # type: ignore[arg-type]

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[object, object]:
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.YAMLError("chave YAML duplicada")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _Entry:
    __slots__ = ("path", "summary")

    def __init__(self, path: Path, summary: RunbookSummary) -> None:
        self.path = path
        self.summary = summary


class RunbookRepository:
    """Índice somente leitura para a árvore determinística do LocalProvider."""

    def __init__(
        self,
        root: Path,
        max_documents: int,
        max_file_bytes: int,
        cache_ttl_seconds: float = 5.0,
    ) -> None:
        self._root = root
        self._max_documents = max_documents
        self._max_file_bytes = max_file_bytes
        self._cache_ttl_seconds = cache_ttl_seconds
        self._entries: tuple[_Entry, ...] = ()
        self._published_ids: frozenset[str] = frozenset()
        self._cache_deadline = 0.0
        self._async_lock = asyncio.Lock()
        self._sync_lock = Lock()
        self._markdown = MarkdownIt(
            "commonmark", {"html": False, "linkify": False, "typographer": False}
        ).enable(["table", "strikethrough"])

    async def list_runbooks(
        self, published_ids: frozenset[str]
    ) -> tuple[RunbookSummary, ...]:
        entries = _latest_revisions(
            [
                entry
                for entry in await self._snapshot(published_ids)
                if entry.summary.id in published_ids
            ]
        )
        return tuple(
            sorted(
                (entry.summary for entry in entries.values()),
                key=lambda item: (item.created_at, item.id),
                reverse=True,
            )
        )

    async def get_runbook(
        self, runbook_id: str, published_ids: frozenset[str]
    ) -> RunbookDocument | None:
        canonical_id = _canonical_uuid(runbook_id)
        if canonical_id is None:
            return None
        entries = _latest_revisions(
            [
                entry
                for entry in await self._snapshot(published_ids)
                if entry.summary.id in published_ids
            ]
        )
        entry = entries.get(canonical_id)
        if entry is None:
            return None
        try:
            text = await asyncio.to_thread(self._read_safe, entry.path)
            summary, body = self._parse_document(entry.path, text)
            if (
                summary.root_id != canonical_id
                or summary.id != entry.summary.id
                or summary.revision != entry.summary.revision
                or summary.replaces != entry.summary.replaces
            ):
                raise ValueError("runbook mudou durante a leitura")
            summary = replace(
                summary,
                root_domain_function=entry.summary.root_domain_function,
            )
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            logger.warning("runbook inválido ignorado: %s", entry.path.name)
            return None
        rendered = self._markdown.render(body)
        sanitized = bleach.clean(
            rendered,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            protocols=_ALLOWED_PROTOCOLS,
            strip=True,
        )
        return RunbookDocument(
            summary=summary,
            html=sanitized,
            markdown=body,
            body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )

    def invalidate(self) -> None:
        """Faz a próxima leitura reconstruir o snapshot após uma revisão."""

        self._cache_deadline = 0.0

    async def _snapshot(
        self, published_ids: frozenset[str]
    ) -> tuple[_Entry, ...]:
        if (
            published_ids == self._published_ids
            and time.monotonic() < self._cache_deadline
        ):
            return self._entries
        async with self._async_lock:
            if (
                published_ids != self._published_ids
                or time.monotonic() >= self._cache_deadline
            ):
                entries = await asyncio.to_thread(self._scan, published_ids)
                # A troca da referência publica sempre um snapshot completo.
                self._entries = entries
                self._published_ids = published_ids
                self._cache_deadline = time.monotonic() + self._cache_ttl_seconds
        return self._entries

    def _scan(self, published_ids: frozenset[str]) -> tuple[_Entry, ...]:
        with self._sync_lock:
            if len(published_ids) > self._max_documents:
                raise CatalogLimitError(
                    "quantidade de runbooks publicados excede o limite configurado"
                )
            if not self._root.exists():
                return ()
            if self._root.is_symlink() or not self._root.is_dir():
                raise OSError("raiz de runbooks inválida")

            discovered: list[_Entry] = []
            for file_entry in _runbook_file_entries(self._root):
                path = Path(file_entry.path)
                canonical_id = _runbook_id_from_stem(path.stem)
                if (
                    path.suffix != ".md"
                    or canonical_id is None
                    or canonical_id not in published_ids
                ):
                    continue
                if len(discovered) >= self._max_documents:
                    raise CatalogLimitError(
                        "quantidade de runbooks excede o limite configurado"
                    )
                try:
                    text = self._read_safe(path)
                    summary, _ = self._parse_document(path, text)
                except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                    logger.warning("runbook inválido ignorado: %s", path.name)
                    continue
                discovered.append(_Entry(path, summary))
            return tuple(discovered)

    def _read_safe(self, path: Path) -> str:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise OSError("runbook não é arquivo regular")
            if file_stat.st_size > self._max_file_bytes:
                raise ValueError("runbook excede o limite")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read(self._max_file_bytes + 1)
            if len(content) > self._max_file_bytes:
                raise ValueError("runbook excede o limite")
            return content.decode("utf-8", errors="strict")
        finally:
            os.close(descriptor)

    @staticmethod
    def _parse_document(path: Path, text: str) -> tuple[RunbookSummary, str]:
        if not text.startswith("---\n"):
            raise ValueError("frontmatter ausente")
        closing = text.find("\n---\n", 4)
        if closing == -1 or closing > 16 * 1024:
            raise ValueError("frontmatter inválido")
        raw_metadata = text[4:closing]
        body = text[closing + 5 :]
        if not body.strip():
            raise ValueError("corpo vazio")
        metadata = yaml.load(raw_metadata, Loader=_StrictSafeLoader)
        if not isinstance(metadata, dict) or frozenset(metadata) not in (
            _ACCEPTED_FRONTMATTER_KEYS
        ):
            raise ValueError("schema de frontmatter inválido")

        runbook_id = metadata["id"]
        author = metadata["autor"]
        role = metadata["nivel_autor"]
        domain = metadata["funcao"]
        created_at_raw = metadata["data_criacao"]
        tags_raw = metadata["tags_inferidas"]
        canonical_id = _canonical_uuid(runbook_id) if isinstance(runbook_id, str) else None
        if canonical_id is None or canonical_id != _runbook_id_from_stem(path.stem):
            raise ValueError("id divergente do arquivo")
        if not isinstance(author, str) or not _valid_author_label(author):
            raise ValueError("autor inválido")
        if not isinstance(role, str) or role not in _ROLES:
            raise ValueError("nível inválido")
        if not isinstance(domain, str) or _DOMAIN_PATTERN.fullmatch(domain) is None:
            raise ValueError("função inválida")
        if not isinstance(created_at_raw, str):
            raise ValueError("data inválida")
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("data inválida") from error
        if created_at.tzinfo is None:
            raise ValueError("data sem fuso horário")
        if (
            not isinstance(tags_raw, list)
            or not 1 <= len(tags_raw) <= 12
            or any(
                not isinstance(tag, str) or _TAG_PATTERN.fullmatch(tag) is None
                for tag in tags_raw
            )
            or len(set(tags_raw)) != len(tags_raw)
        ):
            raise ValueError("tags inválidas")

        # O criterio e a presenca da linhagem, nao a forma exata do conjunto:
        # publicacao e revisao existem tanto no schema legado quanto no atual,
        # e comparar conjuntos aqui obriga a mexer nesta condicao toda vez que
        # o Hub acrescenta um campo.
        if "runbook_raiz" not in metadata:
            root_id = canonical_id
            revision = 1
            replaces = None
        else:
            root_raw = metadata["runbook_raiz"]
            revision_raw = metadata["revisao"]
            replaces_raw = metadata["substitui"]
            root_id = _canonical_uuid(root_raw) if isinstance(root_raw, str) else None
            replaces = (
                _canonical_uuid(replaces_raw)
                if isinstance(replaces_raw, str)
                else None
            )
            if (
                root_id is None
                or replaces is None
                or isinstance(revision_raw, bool)
                or not isinstance(revision_raw, int)
                or not 2 <= revision_raw <= 1_000_000
                or canonical_id in {root_id, replaces}
            ):
                raise ValueError("metadados de revisão inválidos")
            revision = revision_raw

        title = _extract_title(body, canonical_id)
        return (
            RunbookSummary(
                id=canonical_id,
                root_id=root_id,
                revision=revision,
                replaces=replaces,
                title=title,
                author=author,
                author_level=role,
                domain_function=domain,
                root_domain_function=domain,
                created_at=created_at,
                tags=tuple(tags_raw),
            ),
            body,
        )


def _safe_children(path: Path) -> tuple[os.DirEntry[str], ...]:
    with os.scandir(path) as iterator:
        return tuple(sorted(iterator, key=lambda entry: entry.name))


def _canonical_uuid(value: str) -> str | None:
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None
    return canonical if value == canonical else None


def _runbook_id_from_stem(stem: str) -> str | None:
    match = _NAMED_RUNBOOK_PATTERN.fullmatch(stem)
    if match is None:
        return None
    return _canonical_uuid(match.group("id"))


def _runbook_file_entries(root: Path) -> Iterator[os.DirEntry[str]]:
    """Percorre somente domain/ano e o legado ano/mês, sem seguir symlinks."""

    for first in _safe_children(root):
        if not first.is_dir(follow_symlinks=False):
            continue
        if _DOMAIN_PATTERN.fullmatch(first.name):
            for year in _safe_children(Path(first.path)):
                if not year.is_dir(
                    follow_symlinks=False
                ) or not _YEAR_PATTERN.fullmatch(year.name):
                    continue
                for entry in _safe_children(Path(year.path)):
                    if entry.is_file(follow_symlinks=False):
                        yield entry
            continue
        if not _YEAR_PATTERN.fullmatch(first.name):
            continue
        for month in _safe_children(Path(first.path)):
            if not month.is_dir(
                follow_symlinks=False
            ) or not _MONTH_PATTERN.fullmatch(month.name):
                continue
            for entry in _safe_children(Path(month.path)):
                if entry.is_file(follow_symlinks=False):
                    yield entry


def _extract_title(body: str, runbook_id: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            title = " ".join(line[2:].split())[:160]
            if title:
                return title
    return f"Runbook {runbook_id[:8]}"


def _latest_revisions(discovered: list[_Entry]) -> dict[str, _Entry]:
    """Seleciona apenas a cadeia contígua mais nova de cada runbook raiz."""

    grouped: dict[str, list[_Entry]] = {}
    for entry in discovered:
        grouped.setdefault(entry.summary.root_id, []).append(entry)

    latest: dict[str, _Entry] = {}
    for root_id, candidates in grouped.items():
        by_revision: dict[int, list[_Entry]] = {}
        for candidate in candidates:
            by_revision.setdefault(candidate.summary.revision, []).append(candidate)
        roots = by_revision.get(1, [])
        if (
            len(roots) != 1
            or roots[0].summary.id != root_id
            or roots[0].summary.replaces is not None
        ):
            logger.warning("cadeia de revisões inválida ignorada: %s", root_id)
            continue
        current = roots[0]
        next_revision = 2
        while next_revision in by_revision:
            revisions = by_revision[next_revision]
            if (
                len(revisions) != 1
                or revisions[0].summary.replaces != current.summary.id
            ):
                logger.warning("cadeia de revisões ambígua interrompida: %s", root_id)
                break
            current = revisions[0]
            next_revision += 1
        latest[root_id] = _Entry(
            current.path,
            replace(
                current.summary,
                root_domain_function=roots[0].summary.domain_function,
            ),
        )
    return latest
