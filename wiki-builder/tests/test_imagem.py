"""O que o serviço lê em execução precisa estar dentro da imagem.

Esta lacuna já custou uma parada em produção. Ao trocar `requirements.txt` por
`requirements.lock` no Dockerfile, o arquivo que `builder_fingerprint` lê a cada
ciclo sumiu da imagem. Os 31 testes do serviço passaram -- eles exercitam o
código, não o artefato -- e o contêiner entrou em laço de reinício com
`No such file or directory: '/app/requirements.txt'`.

O teste não constrói a imagem: ele lê o Dockerfile e confere que cada arquivo da
impressão digital chega a `/app`.
"""

import re
from pathlib import Path

from app.main import ARQUIVOS_DA_IMPRESSAO

RAIZ = Path(__file__).resolve().parents[1]
DOCKERFILE = RAIZ / "Dockerfile"

# `COPY <origem> <destino>`, ignorando --from e outras flags.
_COPY = re.compile(r"^COPY\s+(?:--[^\s]+\s+)*(\S+)\s+(\S+)\s*$", re.M)


def _destinos_copiados() -> list[str]:
    texto = DOCKERFILE.read_text(encoding="utf-8")
    return [destino for _, destino in _COPY.findall(texto)]


def _chega_na_imagem(relativo: Path, destinos: list[str]) -> bool:
    alvo = f"/app/{relativo.as_posix()}"
    for destino in destinos:
        normalizado = destino.rstrip("/")
        if normalizado == alvo:
            return True
        # `COPY app /app/app` cobre `/app/app/mkdocs_hook.py`.
        if alvo.startswith(normalizado + "/"):
            return True
    return False


def test_arquivos_da_impressao_digital_entram_na_imagem() -> None:
    destinos = _destinos_copiados()
    assert destinos, "nenhum COPY encontrado no Dockerfile"

    ausentes = [
        relativo.as_posix()
        for relativo in ARQUIVOS_DA_IMPRESSAO
        if not _chega_na_imagem(relativo, destinos)
    ]
    assert not ausentes, (
        "o serviço lê estes arquivos em /app a cada ciclo, mas o Dockerfile não "
        f"os copia: {ausentes}"
    )


def test_arquivos_da_impressao_digital_existem_no_repositorio() -> None:
    """Copiar um arquivo que não existe faz a construção falhar, não o
    contêiner -- mas o erro fica igualmente longe da causa."""
    ausentes = [
        relativo.as_posix()
        for relativo in ARQUIVOS_DA_IMPRESSAO
        if not (RAIZ / relativo).is_file()
    ]
    assert not ausentes, f"declarados na impressão digital e inexistentes: {ausentes}"
