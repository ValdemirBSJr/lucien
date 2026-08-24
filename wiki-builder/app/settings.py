"""Leitura e validação estrita da configuração do builder."""

from __future__ import annotations

import os
import re
import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


class SettingsError(ValueError):
    """Indica configuração insegura ou inconsistente."""


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SAFE_DOMAIN_FUNCTION = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _domain_functions(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Areas declaradas em RUNBOOK_DOMAIN_FUNCTIONS, a mesma do Hub.

    O builder so precisa dela para listar no indice as areas que ainda nao
    tem runbook. Uma area ausente aqui nao esconde conteudo: o que estiver no
    disco continua sendo indexado.

    Vazio e legitimo -- o indice cai para descobrir tudo pelo disco -- entao
    uma entrada malformada e ignorada em vez de derrubar o builder. Quem
    valida essa variavel a serio e o Hub, que a usa para autorizar.
    """

    bruto = environment.get("RUNBOOK_DOMAIN_FUNCTIONS", "")
    areas: list[str] = []
    for entrada in bruto.split(","):
        area = entrada.strip()
        if _SAFE_DOMAIN_FUNCTION.fullmatch(area) and area not in areas:
            areas.append(area)
    return tuple(areas)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise SettingsError(f"{name} é obrigatório")
    return value


def _integer(
    environment: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = environment.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} deve ser um inteiro") from exc
    if not minimum <= value <= maximum:
        raise SettingsError(f"{name} deve estar entre {minimum} e {maximum}")
    return value


def _absolute_path(environment: Mapping[str, str], name: str, default: str) -> Path:
    path = Path(environment.get(name, default).strip())
    if not path.is_absolute() or path == Path("/"):
        raise SettingsError(f"{name} deve ser um caminho absoluto diferente de /")
    return path


def _validate_repository_url(value: str) -> str:
    if any(character in value for character in ("\r", "\n", "\\", "%")):
        raise SettingsError("WIKI_REPOSITORY_URL contém caracteres proibidos")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SettingsError("WIKI_REPOSITORY_URL deve usar HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise SettingsError("credenciais não podem fazer parte de WIKI_REPOSITORY_URL")
    if parsed.query or parsed.fragment:
        raise SettingsError("WIKI_REPOSITORY_URL não aceita query string ou fragmento")
    try:
        parsed.port
    except ValueError as exc:
        raise SettingsError("porta inválida em WIKI_REPOSITORY_URL") from exc

    hostname = parsed.hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if (
            len(hostname) > 253
            or any(
                not label
                or len(label) > 63
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                for label in labels
            )
        ):
            raise SettingsError("hostname inválido em WIKI_REPOSITORY_URL") from None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        "//" in parsed.path
        or not parsed.path.endswith(".git")
        or len(segments) < 2
        or not segments[-1].endswith(".git")
    ):
        raise SettingsError("WIKI_REPOSITORY_URL deve apontar para um repositório .git")
    if any(not _SAFE_SEGMENT.fullmatch(segment) for segment in segments):
        raise SettingsError("caminho inseguro em WIKI_REPOSITORY_URL")
    return value


def _validate_branch(value: str) -> str:
    forbidden = ("..", "//", "@{", "\\")
    if (
        not _SAFE_BRANCH.fullmatch(value)
        or any(fragment in value for fragment in forbidden)
        or value.endswith(("/", ".", ".lock"))
    ):
        raise SettingsError("WIKI_REPOSITORY_BRANCH é inválida")
    return value


def _validate_secret(value: str, name: str) -> str:
    if not 8 <= len(value) <= 4096 or any(character in value for character in "\r\n\x00"):
        raise SettingsError(f"{name} possui formato inválido")
    return value


def _validate_regular_file(path: Path, name: str, maximum_bytes: int = 65_536) -> Path:
    if path.is_symlink() or not path.is_file():
        raise SettingsError(f"{name} deve apontar para um arquivo regular")
    if path.stat().st_size > maximum_bytes:
        raise SettingsError(f"{name} excede o limite permitido")
    return path


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuração imutável, sem autoridade proveniente do repositório."""

    repository_url: str
    repository_branch: str
    repository_user: str
    repository_token: str | None = field(default=None, repr=False, compare=False)
    repository_token_file: Path | None = field(default=None, repr=False, compare=False)
    gitea_ca_file: Path | None = None
    poll_seconds: int = 60
    build_timeout_seconds: int = 300
    max_repository_bytes: int = 268_435_456
    max_source_files: int = 10_000
    max_source_bytes: int = 67_108_864
    max_file_bytes: int = 4_194_304
    max_site_bytes: int = 134_217_728
    release_retention: int = 5
    workspace: Path = Path("/workspace")
    publish_root: Path = Path("/publish")
    # Areas declaradas, para o indice mostrar as que ainda estao vazias.
    domain_functions: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environment is None else environment
        repository_url = _validate_repository_url(_required(env, "WIKI_REPOSITORY_URL"))
        repository_branch = _validate_branch(
            env.get("WIKI_REPOSITORY_BRANCH", "main").strip()
        )
        repository_user = _required(env, "WIKI_REPOSITORY_USER")
        if not _SAFE_USER.fullmatch(repository_user):
            raise SettingsError("WIKI_REPOSITORY_USER possui formato inválido")

        token_value = env.get("WIKI_REPOSITORY_TOKEN", "")
        token_file_value = env.get("WIKI_REPOSITORY_TOKEN_FILE", "").strip()
        if bool(token_value) == bool(token_file_value):
            raise SettingsError(
                "informe exatamente uma das variáveis WIKI_REPOSITORY_TOKEN ou "
                "WIKI_REPOSITORY_TOKEN_FILE"
            )

        token: str | None = None
        token_file: Path | None = None
        if token_value:
            token = _validate_secret(token_value, "WIKI_REPOSITORY_TOKEN")
        else:
            token_file = Path(token_file_value)
            if not token_file.is_absolute():
                raise SettingsError("WIKI_REPOSITORY_TOKEN_FILE deve ser absoluto")
            _validate_regular_file(token_file, "WIKI_REPOSITORY_TOKEN_FILE", 4096)
            _validate_secret(
                token_file.read_text(encoding="utf-8").rstrip("\n"),
                "WIKI_REPOSITORY_TOKEN_FILE",
            )

        ca_value = env.get("WIKI_GITEA_CA_FILE", "").strip()
        ca_file = None
        if ca_value:
            ca_file = Path(ca_value)
            if not ca_file.is_absolute():
                raise SettingsError("WIKI_GITEA_CA_FILE deve ser absoluto")
            _validate_regular_file(ca_file, "WIKI_GITEA_CA_FILE")

        workspace = _absolute_path(env, "WIKI_WORKSPACE", "/workspace")
        publish_root = _absolute_path(env, "WIKI_PUBLISH_ROOT", "/publish")
        if workspace == publish_root or workspace in publish_root.parents or publish_root in workspace.parents:
            raise SettingsError("WIKI_WORKSPACE e WIKI_PUBLISH_ROOT não podem se sobrepor")

        return cls(
            domain_functions=_domain_functions(env),
            repository_url=repository_url,
            repository_branch=repository_branch,
            repository_user=repository_user,
            repository_token=token,
            repository_token_file=token_file,
            gitea_ca_file=ca_file,
            poll_seconds=_integer(env, "WIKI_POLL_SECONDS", 60, 15, 3600),
            build_timeout_seconds=_integer(
                env, "WIKI_BUILD_TIMEOUT_SECONDS", 300, 30, 1800
            ),
            max_repository_bytes=_integer(
                env,
                "WIKI_MAX_REPOSITORY_BYTES",
                268_435_456,
                1_048_576,
                2_147_483_648,
            ),
            max_source_files=_integer(
                env, "WIKI_MAX_SOURCE_FILES", 10_000, 1, 100_000
            ),
            max_source_bytes=_integer(
                env,
                "WIKI_MAX_SOURCE_BYTES",
                67_108_864,
                1_048_576,
                1_073_741_824,
            ),
            max_file_bytes=_integer(
                env, "WIKI_MAX_FILE_BYTES", 4_194_304, 1024, 67_108_864
            ),
            max_site_bytes=_integer(
                env,
                "WIKI_MAX_SITE_BYTES",
                134_217_728,
                1_048_576,
                1_073_741_824,
            ),
            release_retention=_integer(env, "WIKI_RELEASE_RETENTION", 5, 2, 20),
            workspace=workspace,
            publish_root=publish_root,
        )
