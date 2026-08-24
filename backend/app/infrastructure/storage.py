import asyncio
import base64
import os
import re
import ssl
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from app.config import Settings
from app.domain.models import PublishedArtifact
from app.domain.ports import (
    ConflictError,
    NotFoundError,
    StorageProvider,
    UpstreamError,
)


_SAFE_JOB_ID = re.compile(r"^[0-9a-f-]{36}$")
_SAFE_GIT_DOCS_PREFIX = re.compile(r"^[A-Za-z0-9._/-]+$")
_SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SAFE_DOMAIN_FUNCTION = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SESSION_SUFFIX = re.compile(r"-\d{8}-\d{6}-[0-9a-f]{12}$")


def clean_artifact_name(job_name: str) -> str:
    """Remove somente o sufixo de sessão produzido pelo próprio CLI."""

    cleaned = _SESSION_SUFFIX.sub("", job_name)
    if _SAFE_ARTIFACT_NAME.fullmatch(cleaned) is None:
        raise ConflictError("nome do Job inválido para publicação")
    return cleaned


# Espelha String(80) em JobRow.name. Nome maior nao caberia na coluna, e a
# gravacao falharia depois de todo o trabalho de revisao ja feito.
_LIMITE_NOME_JOB = 80


def revision_artifact_name(
    root_name: str | None, revision_number: int, root_job_id: str
) -> str:
    """Nome da revisão, legível por quem procura o documento.

    O esquema anterior, `revision-<uuid-da-raiz>-r<n>`, era exato e ilegível:
    o arquivo publicado não dizia de que runbook era a revisão, e os runbooks
    são lidos por gente. O nome passa a ser o da raiz mais `-version-<n>`.

    A base é sempre o nome da **raiz**, nunca o do antecessor imediato: a
    revisão 3 nasce da 2, e encadear daria `...-version-2-version-3`.

    Sem raiz utilizável -- nome que não serve de arquivo, ou linha antiga sem
    ela -- volta ao esquema por UUID. Ele não é bonito, mas nunca falha, e uma
    revisão não pode ser recusada por causa do nome do documento de origem.
    """
    sufixo = f"-version-{revision_number}"
    base = ""
    if root_name is not None:
        try:
            base = clean_artifact_name(root_name)
        except ConflictError:
            base = ""
    base = base[: _LIMITE_NOME_JOB - len(sufixo)].rstrip("-._")
    if not base:
        return f"revision-{root_job_id}-r{revision_number}"
    return f"{base}{sufixo}"


def playbook_relative_path(
    job_id: str,
    created_at: datetime,
    artifact_name: str | None = None,
    domain_function: str | None = None,
) -> Path:
    if not _SAFE_JOB_ID.fullmatch(job_id):
        raise ConflictError("identificador de Job inválido para publicação")
    filename = f"{job_id}.md"
    if artifact_name is not None:
        # O UUID completo preserva a identidade única do Job no caminho.
        filename = f"{clean_artifact_name(artifact_name)}--{job_id}.md"
    if domain_function is None:
        # Compatibilidade apenas para chamadas legadas; produção sempre informa
        # o domínio confiável congelado pelo Hub.
        return Path(f"{created_at.year:04d}") / f"{created_at.month:02d}" / filename
    if _SAFE_DOMAIN_FUNCTION.fullmatch(domain_function) is None:
        raise ConflictError("domínio inválido para publicação")
    return Path(f"{created_at.year:04d}") / domain_function / filename


def legacy_playbook_relative_paths(
    job_id: str,
    created_at: datetime,
    artifact_name: str | None = None,
    domain_function: str | None = None,
) -> tuple[Path, ...]:
    """Caminhos de gerações anteriores, em ordem de probabilidade.

    Só a leitura consulta estes caminhos. Publicações antigas continuam onde
    foram gravadas -- o artefato é imutável e a URL pode já estar anotada em
    outro lugar -- então revisá-las exige encontrá-las no layout da época.

    Duas gerações precedem o atual `<ano>/<domínio>`:

    - `<domínio>/<ano>`, usado antes da inversão;
    - `<ano>/<mês>`, de quando a publicação ainda não congelava o domínio.

    A escrita nunca usa nenhum deles.
    """

    atual = playbook_relative_path(
        job_id, created_at, artifact_name, domain_function
    )
    candidatos: list[Path] = []
    if domain_function is not None:
        candidatos.append(
            Path(domain_function) / f"{created_at.year:04d}" / atual.name
        )
    por_mes = (
        Path(f"{created_at.year:04d}") / f"{created_at.month:02d}" / atual.name
    )
    if por_mes != atual:
        candidatos.append(por_mes)
    return tuple(candidatos)



def git_playbook_relative_path(
    docs_prefix: str,
    job_id: str,
    created_at: datetime,
    artifact_name: str | None = None,
    domain_function: str | None = None,
) -> PurePosixPath:
    """Mantém os artefatos Git dentro da árvore publicada pelo MkDocs."""

    prefix = PurePosixPath(docs_prefix)
    if (
        prefix.is_absolute()
        or _SAFE_GIT_DOCS_PREFIX.fullmatch(docs_prefix) is None
        or any(part in {"", ".", ".."} for part in prefix.parts)
    ):
        raise ConflictError("prefixo Git inválido para publicação")
    relative = PurePosixPath(
        playbook_relative_path(
            job_id, created_at, artifact_name, domain_function
        ).as_posix()
    )
    return prefix / relative


class LocalProvider(StorageProvider):
    """Strategy local com escrita atômica e verificação de conteúdo existente."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def publish(
        self,
        job_id: str,
        created_at: datetime,
        markdown: str,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> PublishedArtifact:
        relative = playbook_relative_path(
            job_id, created_at, artifact_name, domain_function
        )
        return await asyncio.to_thread(self._publish_sync, relative, markdown)

    async def read_published(
        self,
        job_id: str,
        created_at: datetime,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> str:
        relative = playbook_relative_path(
            job_id, created_at, artifact_name, domain_function
        )
        candidatos = (
            relative,
            *legacy_playbook_relative_paths(
                job_id, created_at, artifact_name, domain_function
            ),
        )
        for candidato in candidatos:
            try:
                return await asyncio.to_thread(self._read_sync, candidato)
            except NotFoundError:
                continue
        raise NotFoundError("artefato publicado nao encontrado")

    def _read_sync(self, relative: Path) -> str:
        target = (self._root / relative).resolve()
        # Mesma verificacao da escrita: um caminho derivado nao pode escapar
        # da raiz, ainda que a leitura pareca inofensiva.
        if self._root not in target.parents:
            raise ConflictError("caminho de leitura escapou da raiz permitida")
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise NotFoundError("artefato publicado nao encontrado") from error

    def _publish_sync(self, relative: Path, markdown: str) -> PublishedArtifact:
        target = (self._root / relative).resolve()
        if self._root not in target.parents:
            raise ConflictError("caminho de publicação escapou da raiz permitida")
        content = markdown.encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)

        if target.exists():
            if target.read_bytes() != content:
                raise ConflictError("playbook já existe com conteúdo diferente")
            return PublishedArtifact(url=f"local://{relative.as_posix()}")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                os.chmod(temporary_name, 0o640)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                # O hard link publica o inode completo somente se o destino ainda
                # não existir. Diferente de os.replace, nunca sobrescreve uma
                # publicação concorrente.
                os.link(temporary_name, target)
            except FileExistsError:
                if target.read_bytes() != content:
                    raise ConflictError(
                        "playbook já existe com conteúdo diferente"
                    )
            else:
                # Persiste a entrada de diretório após o conteúdo já ter passado
                # por fsync. O Hub é executado em contêiner Linux.
                directory_descriptor = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return PublishedArtifact(url=f"local://{relative.as_posix()}")


class GitContentProvider(StorageProvider):
    """Base compatível com a API de Contents usada por GitHub e Gitea."""

    def __init__(
        self,
        api_base: str,
        owner: str,
        repository: str,
        branch: str,
        token: str,
        authorization_scheme: str,
        docs_prefix: str,
        ca_file: Path | None,
    ) -> None:
        if not all((api_base, owner, repository, branch, token)):
            raise ValueError("configuração Git incompleta")
        self._api_base = api_base.rstrip("/")
        self._owner = owner
        self._repository = repository
        self._branch = branch
        self._docs_prefix = docs_prefix
        self._headers = {
            "Authorization": f"{authorization_scheme} {token}",
            "Accept": "application/json",
        }
        self._ssl_context = ssl.create_default_context()
        if ca_file is not None:
            try:
                # load_verify_locations acrescenta a CA corporativa às raízes
                # públicas já carregadas por create_default_context().
                self._ssl_context.load_verify_locations(cafile=str(ca_file))
            except (OSError, ssl.SSLError) as error:
                raise ValueError("não foi possível carregar GIT_CA_FILE") from error
        self._client: httpx.AsyncClient | None = None

    def _cliente(self) -> httpx.AsyncClient:
        """Um cliente por provider, criado no primeiro uso.

        Um AsyncClient por publicação refaz o handshake TLS a cada artefato e
        descarta o pool de conexões. Criar no __init__ prenderia o cliente ao
        laço vigente na construção -- que nos testes e nos utilitários de linha
        de comando não é o laço que atende as requisições.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers=self._headers,
                verify=self._ssl_context,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _corpo(response: httpx.Response) -> dict[str, object]:
        """Lê o JSON da resposta sem confiar que ela seja JSON.

        Proxy corporativo, gateway e página de manutenção respondem HTML com o
        status que quiserem. `response.json()` levanta ValueError nesse caso, e
        sem tradução a exceção atravessa a camada de armazenamento: o Hub
        devolve 500 e o cliente não distingue provedor indisponível -- que se
        resolve repetindo -- de defeito nosso.
        """
        try:
            data = response.json()
        except ValueError as error:
            raise UpstreamError("provedor Git respondeu com corpo inválido") from error
        if not isinstance(data, dict):
            raise UpstreamError("provedor Git respondeu com estrutura inesperada")
        return data

    @staticmethod
    def _conteudo(data: dict[str, object]) -> bytes:
        bruto = data.get("content")
        if not isinstance(bruto, str):
            raise UpstreamError("provedor Git retornou conteúdo inválido")
        # GitHub devolve o base64 quebrado em linhas; Gitea, numa linha só.
        # Remover o espaço em branco antes permite exigir validate=True: sem
        # ele, base64 descarta caractere fora do alfabeto em silêncio e uma
        # resposta truncada vira bytes plausíveis que passariam por artefato.
        try:
            return base64.b64decode("".join(bruto.split()), validate=True)
        except ValueError as error:
            raise UpstreamError("provedor Git retornou conteúdo inválido") from error

    @staticmethod
    def _inacessivel(error: httpx.HTTPError) -> UpstreamError:
        # Timeout, DNS, conexão recusada e falha de TLS chegam como HTTPError.
        return UpstreamError(f"provedor Git inacessível: {type(error).__name__}")

    async def _confirmar(
        self, client: httpx.AsyncClient, url: str, expected: bytes
    ) -> PublishedArtifact | None:
        """Relê o destino para saber se uma escrita incerta chegou.

        Devolve None quando o artefato não está lá -- inclusive se a própria
        releitura falhar, porque aí já existe uma falha a reportar e insistir
        não acrescenta informação.
        """
        try:
            existing = await self._read_existing(client, url)
        except UpstreamError:
            return None
        if existing is None:
            return None
        content, public_url = existing
        if content != expected:
            # Corrida perdida é conflito permanente, não indisponibilidade.
            raise ConflictError("playbook remoto já existe com conteúdo diferente")
        return PublishedArtifact(url=public_url)

    async def publish(
        self,
        job_id: str,
        created_at: datetime,
        markdown: str,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> PublishedArtifact:
        url = self._contents_url(
            job_id, created_at, artifact_name, domain_function
        )
        expected = markdown.encode("utf-8")

        client = self._cliente()
        # A leitura inicial falha alto de propósito: se o provedor está fora,
        # publicar por cima às cegas é pior que recusar e deixar a fila repetir.
        existing = await self._read_existing(client, url)
        if existing is not None:
            content, public_url = existing
            if content != expected:
                raise ConflictError("playbook remoto já existe com conteúdo diferente")
            return PublishedArtifact(url=public_url)

        payload = {
            "message": f"docs: publica runbook {job_id}",
            "content": base64.b64encode(expected).decode("ascii"),
            "branch": self._branch,
        }
        try:
            response = await client.put(url, json=payload)
        except httpx.HTTPError as error:
            # A escrita pode ter chegado antes de a resposta se perder. Reler
            # separa "não publicou" de "publicou e não soubemos": sem isso a
            # fila repete e o retry encontra o arquivo já lá.
            confirmado = await self._confirmar(client, url, expected)
            if confirmado is not None:
                return confirmado
            raise self._inacessivel(error) from error
        if response.status_code in {409, 422}:
            # Reconcilia corrida/retry após timeout: o sucesso remoto pode ter
            # ocorrido mesmo com o provedor recusando esta tentativa.
            confirmado = await self._confirmar(client, url, expected)
            if confirmado is not None:
                return confirmado
        if response.status_code not in {200, 201}:
            raise UpstreamError(
                f"provedor Git recusou publicação (HTTP {response.status_code})"
            )
        conteudo = self._corpo(response).get("content")
        publicado = url
        if isinstance(conteudo, dict):
            for chave in ("html_url", "download_url"):
                valor = conteudo.get(chave)
                if isinstance(valor, str) and valor:
                    publicado = valor
                    break
        return PublishedArtifact(url=publicado)

    async def read_published(
        self,
        job_id: str,
        created_at: datetime,
        artifact_name: str | None = None,
        domain_function: str | None = None,
    ) -> str:
        url = self._contents_url(
            job_id, created_at, artifact_name, domain_function
        )
        legados = tuple(
            PurePosixPath(self._docs_prefix) / relativo.as_posix()
            for relativo in legacy_playbook_relative_paths(
                job_id, created_at, artifact_name, domain_function
            )
        )
        client = self._cliente()
        existing = await self._read_existing(client, url)
        # Geracoes anteriores do layout, em ordem de probabilidade.
        for legado in legados:
            if existing is not None:
                break
            existing = await self._read_existing(
                client, self._contents_url_for(legado)
            )
        if existing is None:
            raise NotFoundError("artefato publicado nao encontrado no provedor Git")
        content, _ = existing
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UpstreamError("artefato publicado nao e UTF-8 valido") from error

    def _contents_url(
        self,
        job_id: str,
        created_at: datetime,
        artifact_name: str | None,
        domain_function: str | None,
    ) -> str:
        return self._contents_url_for(
            git_playbook_relative_path(
                self._docs_prefix,
                job_id,
                created_at,
                artifact_name,
                domain_function,
            )
        )

    def _contents_url_for(self, relative: PurePosixPath | Path) -> str:
        caminho = PurePosixPath(relative).as_posix()
        return (
            f"{self._api_base}/repos/{quote(self._owner, safe='')}"
            f"/{quote(self._repository, safe='')}/contents/{caminho}"
        )

    async def _read_existing(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[bytes, str] | None:
        try:
            response = await client.get(url, params={"ref": self._branch})
        except httpx.HTTPError as error:
            raise self._inacessivel(error) from error
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise UpstreamError(
                f"falha ao consultar provedor Git (HTTP {response.status_code})"
            )
        data = self._corpo(response)
        content = self._conteudo(data)
        public_url = url
        for chave in ("html_url", "download_url"):
            valor = data.get(chave)
            if isinstance(valor, str) and valor:
                public_url = valor
                break
        return content, public_url


class GitHubProvider(GitContentProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            api_base=settings.git_api_base,
            owner=settings.git_owner,
            repository=settings.git_repo,
            branch=settings.git_branch,
            token=settings.git_token.get_secret_value(),
            authorization_scheme="Bearer",
            docs_prefix=settings.git_docs_prefix,
            ca_file=settings.git_ca_file,
        )


class GiteaProvider(GitContentProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            api_base=settings.git_api_base,
            owner=settings.git_owner,
            repository=settings.git_repo,
            branch=settings.git_branch,
            token=settings.git_token.get_secret_value(),
            authorization_scheme="token",
            docs_prefix=settings.git_docs_prefix,
            ca_file=settings.git_ca_file,
        )


def build_storage_provider(settings: Settings) -> StorageProvider:
    providers: dict[str, type[StorageProvider] | None] = {
        "local": None,
        "github": GitHubProvider,
        "gitea": GiteaProvider,
    }
    if settings.storage_provider == "local":
        return LocalProvider(settings.local_storage_root)
    provider_type = providers[settings.storage_provider]
    assert provider_type is not None
    return provider_type(settings)  # type: ignore[call-arg]
