"""Traz para o espelho os runbooks publicados antes de ele existir.

O espelho (`published_documents`/`published_assets`) preenche a cada nova
publicação. Sem isto, um acervo antigo continuaria vivendo só no Gitea ou no
GitHub, e migrar para uma wiki local levaria só o que foi publicado depois --
o que esvazia a razão de o espelho existir.

Roda uma vez, é seguro repetir e não toca no repositório: só lê.

    docker compose --env-file .env -f docker-compose.local.yml \\
      exec -T hub python -m app.backfill_mirror

Uma publicação que o repositório não tem mais (arquivo apagado à mão, layout
que nenhuma geração conhecida explica) é relatada e pulada, não interrompe as
demais: parar na primeira obrigaria a consertar o acervo inteiro antes de
espelhar qualquer coisa.
"""

import asyncio
import sys
from pathlib import PurePosixPath

from app.config import Settings
from app.domain.images import previously_published_asset_paths
from app.domain.models import Job
from app.domain.ports import (
    ConflictError,
    MirroredAsset,
    MirroredDocument,
    NotFoundError,
    StorageProvider,
    UpstreamError,
)
from app.infrastructure.database import SQLAlchemyJobRepository
from app.infrastructure.storage import (
    build_storage_provider,
    legacy_playbook_relative_paths,
    playbook_relative_path,
)


async def _localizar(
    storage: StorageProvider, job: Job, dominio: str
) -> tuple[str, str]:
    """Acha onde o artefato está de verdade e devolve (caminho, markdown).

    O layout já mudou duas vezes, e artefato publicado é imutável: quem foi
    gravado em `<domínio>/<ano>` continua lá. Precisamos do caminho, e não só
    do conteúdo, porque os anexos moram ao lado do `.md` -- espelhar o texto
    de um layout e procurar a imagem em outro acharia nada.
    """

    atual = playbook_relative_path(job.id, job.created_at, job.name, dominio)
    candidatos = (
        atual,
        *legacy_playbook_relative_paths(job.id, job.created_at, job.name, dominio),
    )
    for candidato in candidatos:
        caminho = candidato.as_posix()
        try:
            conteudo = await storage.read_bytes(caminho)
        except NotFoundError:
            continue
        return caminho, conteudo.decode("utf-8")
    raise NotFoundError(f"artefato de {job.id} não encontrado em nenhum layout")


async def _anexos(
    storage: StorageProvider, job_id: str, caminho_md: str, markdown: str
) -> tuple[MirroredAsset, ...]:
    """Só os anexos DESTE job, como na publicação ao vivo.

    Uma revisão referencia imagens herdadas do ancestral sem reenviá-las, e o
    espelho não as duplica -- a linha do ancestral é que as guarda. Copiar a
    regra aqui mantém o backfill produzindo o mesmo estado que uma publicação
    de hoje produziria.
    """

    pasta = PurePosixPath(caminho_md).parent / "assets" / job_id
    encontrados: list[MirroredAsset] = []
    for referencia in sorted(previously_published_asset_paths(markdown)):
        prefixo, _, arquivo = referencia.rpartition("/")
        if prefixo != f"assets/{job_id}":
            continue
        caminho = (pasta / arquivo).as_posix()
        conteudo = await storage.read_bytes(caminho)
        encontrados.append(
            MirroredAsset(
                filename=arquivo, relative_path=caminho, content=conteudo
            )
        )
    return tuple(encontrados)


async def espelhar_publicacao(
    repository: SQLAlchemyJobRepository, storage: StorageProvider, job_id: str
) -> int:
    """Espelha uma publicação. Levanta se o artefato não estiver no destino.

    Falhar alto aqui é o ponto: engolir gravaria uma linha vazia e reportaria
    sucesso, e a perda só apareceria na migração para a wiki. Quem decide
    tolerar é o `main`, que pula e relata.
    """

    # `get_published_for_revision` já resolve a raiz da linhagem, e é dela que
    # sai o domínio usado na gravação -- para uma publicação sem revisões a
    # raiz é ela mesma, então serve aos dois casos sem ramificar.
    fonte = await repository.get_published_for_revision(job_id)
    dominio = fonte.root_identity.domain_function
    caminho, markdown = await _localizar(storage, fonte.job, dominio)
    anexos = await _anexos(storage, job_id, caminho, markdown)
    await repository.save_published(
        MirroredDocument(
            job_id=job_id,
            markdown=markdown,
            relative_path=caminho,
            assets=anexos,
        )
    )
    return len(anexos)


async def _backfill() -> tuple[int, int, int]:
    settings = Settings()
    repository = SQLAlchemyJobRepository(settings.database_url)
    storage = build_storage_provider(settings)
    espelhados = 0
    anexos = 0
    falhos = 0
    await repository.initialize()
    try:
        pendentes = await repository.published_ids_without_mirror()
        print(f"{len(pendentes)} publicação(ões) fora do espelho", file=sys.stderr)
        for job_id in pendentes:
            try:
                anexos += await espelhar_publicacao(repository, storage, job_id)
            # ConflictError entra aqui porque é o que `get_published_for_revision`
            # levanta quando a raiz não tem identidade confiável -- publicação
            # anterior à coluna `publication_identity`, que é exatamente o tipo
            # de linha antiga que um backfill encontra.
            except (
                ConflictError,
                NotFoundError,
                UpstreamError,
                UnicodeDecodeError,
            ) as erro:
                print(f"  pulado {job_id}: {erro}", file=sys.stderr)
                falhos += 1
                continue
            espelhados += 1
    finally:
        await storage.aclose()
        await repository.close()
    return espelhados, anexos, falhos


def main() -> None:
    espelhados, anexos, falhos = asyncio.run(_backfill())
    print(
        f"{espelhados} runbook(s) e {anexos} imagem(ns) espelhados; "
        f"{falhos} pulado(s)",
        file=sys.stderr,
    )
    # Sai diferente de zero quando algo ficou para trás: num pipeline, um
    # backfill parcial não pode passar por completo.
    sys.exit(1 if falhos else 0)


if __name__ == "__main__":
    main()
