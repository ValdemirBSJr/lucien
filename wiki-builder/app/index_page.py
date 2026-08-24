"""Índice gerado quando o repositório não traz um `docs/index.md` próprio.

O Hub publica em `<ano>/<área>/arquivo.md` e nunca escreve um índice: o
repositório é conteúdo, não navegação. Sem `index.md` na raiz, o MkDocs sai
com 0 mas não produz `site/index.html`, e `validate_site_tree` recusa a
release. O builder ficava reprovando em silêncio a cada ciclo.

Gerar aqui, e não commitar o arquivo no repositório, mantém o índice sempre
coerente com o conteúdo: um arquivo commitado à mão envelheceria a cada
publicação nova.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

# Assinatura que distingue o índice gerado de um escrito por uma pessoa. Sem
# ela, o segundo ciclo encontraria o próprio arquivo e nunca o atualizaria.
MARKER = "<!-- lucien-wiki-builder: índice gerado automaticamente -->"

_YEAR = re.compile(r"[0-9]{4}")
# `<nome_limpo>--<uuid>.md`, o formato que o Hub publica.
_ARTIFACT_SUFFIX = re.compile(
    r"--[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_MARKDOWN_SPECIAL = re.compile(r"([\\`\[\]<>])")
_SEM_ANO = "Sem ano"
_SEM_AREA = "Sem área"
# Rotulos de fallback: sao posicoes no caminho, nao areas declaraveis.
_ROTULOS_SEM_AREA = frozenset({_SEM_AREA})
# Um repositório absurdamente grande não pode transformar o índice no maior
# arquivo do site. O limite é folgado para o uso real.
_MAX_ENTRIES = 5_000


def ensure_index(docs_dir: Path, known_areas: Sequence[str] = ()) -> bool:
    """Escreve `index.md` quando não houver um do repositório.

    Devolve `True` quando gerou ou atualizou. Um `index.md` sem a assinatura
    pertence ao repositório e é preservado: quem quiser uma capa própria
    apenas commita o arquivo.
    """

    index = docs_dir / "index.md"
    if index.is_symlink():
        # `validate_source_tree` já recusaria, mas escrever através de um link
        # sairia do diretório validado.
        return False
    if index.exists():
        try:
            atual = index.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        if not atual.startswith(MARKER):
            return False

    index.write_text(_render(docs_dir, known_areas), encoding="utf-8")
    return True


def _render(docs_dir: Path, known_areas: Sequence[str] = ()) -> str:
    agrupado: dict[str, dict[str, list[tuple[str, str]]]] = {}
    por_area: dict[str, int] = {}
    total = 0
    for arquivo in sorted(docs_dir.rglob("*.md")):
        if not arquivo.is_file() or arquivo.is_symlink():
            continue
        relativo = arquivo.relative_to(docs_dir)
        if relativo == Path("index.md"):
            continue
        if total >= _MAX_ENTRIES:
            break
        total += 1
        ano, area = _classify(relativo)
        por_area[area] = por_area.get(area, 0) + 1
        agrupado.setdefault(ano, {}).setdefault(area, []).append(
            (_link_text(arquivo.name), _link_target(relativo))
        )

    linhas = [
        MARKER,
        "",
        "# Runbooks",
        "",
        "Índice gerado a cada publicação. Para substituí-lo por uma capa"
        " própria, adicione `docs/index.md` ao repositório.",
        "",
    ]
    linhas.extend(_secao_areas(por_area, known_areas))

    if not agrupado:
        linhas.append("Nenhum runbook publicado até agora.")
        return "\n".join(linhas).rstrip("\n") + "\n"

    for ano in _ordena_anos(agrupado):
        linhas.extend([f"## {ano}", ""])
        for area in sorted(agrupado[ano]):
            linhas.extend([f"### {area}", ""])
            for texto, caminho in sorted(agrupado[ano][area]):
                linhas.append(f"- [{texto}]({caminho})")
            linhas.append("")
    return "\n".join(linhas).rstrip("\n") + "\n"


def _secao_areas(
    por_area: dict[str, int], known_areas: Sequence[str]
) -> list[str]:
    """Lista as areas declaradas, inclusive as que ainda nao tem runbook.

    Uma area recem-criada existe no `.env` e ja e aceita por
    `lucien start -r`, mas nao teria diretorio nenhum no repositorio. Sem
    esta secao ela seria invisivel na wiki ate a primeira publicacao.

    A uniao com o que esta no disco e deliberada: uma area renomeada ou
    removida do `.env` continua tendo conteudo publicado, e esconder esse
    conteudo do indice seria pior do que mostrar uma area fora da lista.
    """

    declaradas = [a for a in known_areas if a not in _ROTULOS_SEM_AREA]
    todas = sorted({*por_area, *declaradas})
    if not todas:
        return []
    linhas = ["## Áreas", ""]
    for area in todas:
        quantidade = por_area.get(area, 0)
        if quantidade == 0:
            linhas.append(f"- **{area}** — nenhum runbook publicado ainda")
        elif quantidade == 1:
            linhas.append(f"- **{area}** — 1 runbook")
        else:
            linhas.append(f"- **{area}** — {quantidade} runbooks")
    linhas.append("")
    return linhas


def _ordena_anos(agrupado: dict[str, object]) -> list[str]:
    # Ano mais recente primeiro; os rótulos não numéricos ficam no fim.
    numericos = sorted((a for a in agrupado if a.isdigit()), reverse=True)
    return numericos + sorted(a for a in agrupado if not a.isdigit())


def _classify(relativo: Path) -> tuple[str, str]:
    """Descobre ano e área pela posição do diretório de ano no caminho.

    Aceita os dois layouts de propósito: `<ano>/<área>/` é o atual e
    `<área>/<ano>/` é o anterior, e um repositório em uso tem os dois.
    """

    pais = relativo.parts[:-1]
    if len(pais) >= 2:
        penultimo, ultimo = pais[-2], pais[-1]
        if _YEAR.fullmatch(ultimo):
            return ultimo, penultimo
        if _YEAR.fullmatch(penultimo):
            return penultimo, ultimo
    if pais and _YEAR.fullmatch(pais[-1]):
        return pais[-1], _SEM_AREA
    return _SEM_ANO, pais[-1] if pais else _SEM_AREA


def _link_text(filename: str) -> str:
    """Nome legível do artefato, escapado para não injetar Markdown.

    O nome vem do repositório, que é dado não confiável para o builder. O
    hook com bleach só limpa o HTML já renderizado, então um `[` solto aqui
    ainda quebraria o link.
    """

    nome = _ARTIFACT_SUFFIX.sub("", Path(filename).stem)
    return _MARKDOWN_SPECIAL.sub(r"\\\1", nome) or "runbook"


def _link_target(relativo: Path) -> str:
    """Percent-encoding do caminho, e nao so do texto.

    Escapar apenas o texto nao basta: um `)` no nome do arquivo fecharia o
    link antes da hora e o resto do caminho vazaria como texto. `quote`
    codifica parenteses e espacos, preservando as barras.
    """

    return quote(relativo.as_posix(), safe="/")
