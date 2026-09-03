"""Regrava a árvore publicada a partir do banco, sem Git nem provedor.

É a contrapartida do espelho (`published_documents`/`published_assets`): sem um
comando que o leia, guardar o conteúdo no banco seria só ocupar espaço. Com
ele, hospedar os runbooks numa wiki local deixa de depender de migrar o
repositório do Gitea ou do GitHub -- o destino vira uma escolha, não uma
amarra.

A saída é um tar em stdout, e não arquivos num diretório, pelo mesmo motivo de
`scripts/backup-db.sh`: o contêiner do Hub roda `read_only`, e o único caminho
gravável dentro dele é um tmpfs de 64 MiB. Quem extrai é o host:

    docker compose --env-file .env -f docker-compose.local.yml \\
      exec -T hub python -m app.export_wiki > wiki.tar
    mkdir -p wiki && tar -xf wiki.tar -C wiki

O conteúdo extraído é a mesma árvore que o provedor `local` produz, que é a que
o wiki-builder consome: o MkDocs sobe direto sobre ela.

Escrever é deliberadamente burro -- caminho gravado, bytes gravados. Toda a
inteligência (domínio, ano, nome do artefato, posição dos anexos) já foi
decidida na publicação e está no caminho. Recalculá-la aqui abriria espaço para
a exportação divergir do que foi publicado de verdade.
"""

import asyncio
import io
import sys
import tarfile
from pathlib import PurePosixPath
from typing import BinaryIO

from app.config import Settings
from app.domain.ports import MirroredDocument
from app.infrastructure.database import SQLAlchemyJobRepository


def _caminho_seguro(relativo: str) -> str:
    """Recusa caminho absoluto ou com `..` antes de entrar no tar.

    O espelho é escrito pelo próprio Hub, mas quem extrai é o host, e um tar
    com `../` escreve fora do diretório de destino. A checagem custa três
    linhas e fica do lado que produz o arquivo.
    """

    caminho = PurePosixPath(relativo)
    if caminho.is_absolute() or any(parte in {"", ".", ".."} for parte in caminho.parts):
        raise ValueError(f"caminho inválido no espelho: {relativo}")
    return caminho.as_posix()


def _membro(nome: str, conteudo: bytes) -> tarfile.TarInfo:
    """Metadados fixos: dois exports do mesmo acervo produzem o mesmo tar.

    Sem isso o horário e o dono do contêiner entrariam no arquivo, e duas
    exportações idênticas ficariam diferentes byte a byte -- o que atrapalha
    justamente quem quer comparar uma com a outra.
    """

    info = tarfile.TarInfo(name=nome)
    info.size = len(conteudo)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def escrever_documento(arquivo: tarfile.TarFile, documento: MirroredDocument) -> int:
    """Grava um runbook e seus anexos no tar. Devolve quantos anexos foram."""

    for anexo in documento.assets:
        nome = _caminho_seguro(anexo.relative_path)
        arquivo.addfile(_membro(nome, anexo.content), io.BytesIO(anexo.content))
    corpo = documento.markdown.encode("utf-8")
    nome = _caminho_seguro(documento.relative_path)
    arquivo.addfile(_membro(nome, corpo), io.BytesIO(corpo))
    return len(documento.assets)


async def _exportar(saida: BinaryIO) -> tuple[int, int]:
    settings = Settings()
    repository = SQLAlchemyJobRepository(settings.database_url)
    documentos = 0
    anexos = 0
    await repository.initialize()
    try:
        # `w|` é o modo de fluxo: escreve enquanto lê, sem precisar voltar no
        # arquivo, que é o que permite mandar direto para stdout.
        with tarfile.open(fileobj=saida, mode="w|") as arquivo:
            async for documento in repository.iter_published_mirror():
                anexos += escrever_documento(arquivo, documento)
                documentos += 1
    finally:
        await repository.close()
    return documentos, anexos


def main() -> None:
    documentos, anexos = asyncio.run(_exportar(sys.stdout.buffer))
    # O relatório vai para stderr: stdout é o tar, e qualquer outra coisa ali
    # corromperia o arquivo.
    print(
        f"{documentos} runbook(s) e {anexos} imagem(ns) exportados",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
