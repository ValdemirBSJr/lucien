"""Valida os contratos de idioma no HTML realmente gerado pelo MkDocs."""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


# O primeiro nome e o caminho canônico em português; o segundo é a URL inglesa
# publicada pelo hook. Os nomes dos arquivos-fonte continuam iguais para que o
# mkdocs-static-i18n consiga parear as duas traduções.
PAGINAS = {
    "documentacao-tecnica": (
        "technical-documentation",
        "Documentação técnica",
        "Technical documentation",
    ),
    "implantacao-isolada": (
        "isolated-deployment",
        "Implantação isolada: CLI, API e TLS",
        "Isolated deployment: CLI, API, and TLS",
    ),
    "manual-instalacao": (
        "installation-manual",
        "Manual de instalação do Lucien",
        "Lucien installation manual",
    ),
    "operacao": (
        "operations",
        "Operação e segurança",
        "Operation and security",
    ),
    "publicacao": (
        "publication",
        "Publicação da wiki",
        "Wiki publication",
    ),
}


class DocumentoHTML(HTMLParser):
    """Extrai somente os elementos necessários para verificar o contrato."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.idioma = ""
        self.titulo: list[str] = []
        self.alternativas: dict[str, str] = {}
        self.links: list[str] = []
        self._em_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = dict(attrs)
        if tag == "html":
            self.idioma = atributos.get("lang") or ""
        elif tag == "h1":
            self._em_h1 = True
        elif tag == "link":
            relacoes = (atributos.get("rel") or "").split()
            idioma = atributos.get("hreflang")
            destino = atributos.get("href")
            if "alternate" in relacoes and idioma and destino:
                self.alternativas[idioma] = destino
        elif tag == "a" and atributos.get("href"):
            self.links.append(atributos["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._em_h1 = False

    def handle_data(self, data: str) -> None:
        if self._em_h1:
            self.titulo.append(data)

    @property
    def titulo_normalizado(self) -> str:
        return " ".join("".join(self.titulo).split())


def caminho_url(site: Path, html: Path) -> str:
    relativo = html.relative_to(site).as_posix()
    if relativo.endswith("/index.html"):
        return f"/{relativo[: -len('index.html')]}"
    return f"/{relativo}"


def resolver_url(site: Path, html: Path, destino: str) -> str:
    base = f"https://documentacao.invalid{caminho_url(site, html)}"
    return urlparse(urljoin(base, destino)).path


def ler_html(arquivo: Path, erros: list[str]) -> DocumentoHTML | None:
    if not arquivo.is_file():
        erros.append(f"HTML ausente: {arquivo}")
        return None
    documento = DocumentoHTML()
    documento.feed(arquivo.read_text(encoding="utf-8"))
    return documento


def exigir(condicao: bool, mensagem: str, erros: list[str]) -> None:
    if not condicao:
        erros.append(mensagem)


def validar(site: Path) -> list[str]:
    erros: list[str] = []
    indice_ingles_path = site / "en" / "index.html"
    indice_ingles = ler_html(indice_ingles_path, erros)
    links_indice = (
        {resolver_url(site, indice_ingles_path, link) for link in indice_ingles.links}
        if indice_ingles
        else set()
    )

    for slug_pt, (slug_en, titulo_pt, titulo_en) in PAGINAS.items():
        pagina_pt_path = site / slug_pt / "index.html"
        pagina_en_path = site / "en" / slug_en / "index.html"
        pagina_legada_en = site / "en" / slug_pt / "index.html"
        pagina_pt = ler_html(pagina_pt_path, erros)
        pagina_en = ler_html(pagina_en_path, erros)

        exigir(
            not pagina_legada_en.exists(),
            f"rota inglesa antiga ainda existe: /en/{slug_pt}/",
            erros,
        )
        exigir(
            f"/en/{slug_en}/" in links_indice,
            f"índice inglês não aponta para /en/{slug_en}/",
            erros,
        )
        exigir(
            f"/en/{slug_pt}/" not in links_indice,
            f"índice inglês ainda aponta para /en/{slug_pt}/",
            erros,
        )

        if pagina_en:
            exigir(
                pagina_en.idioma == "en",
                f"/en/{slug_en}/ usa lang={pagina_en.idioma!r}, esperado 'en'",
                erros,
            )
            exigir(
                titulo_en in pagina_en.titulo_normalizado,
                f"/en/{slug_en}/ não contém o H1 inglês {titulo_en!r}",
                erros,
            )
            exigir(
                titulo_pt not in pagina_en.titulo_normalizado,
                f"/en/{slug_en}/ recebeu conteúdo português por fallback",
                erros,
            )
            href_pt = pagina_en.alternativas.get("pt", "")
            exigir(
                bool(href_pt)
                and resolver_url(site, pagina_en_path, href_pt) == f"/{slug_pt}/",
                f"/en/{slug_en}/ não alterna para /{slug_pt}/",
                erros,
            )

        if pagina_pt:
            exigir(
                titulo_pt in pagina_pt.titulo_normalizado,
                f"/{slug_pt}/ não contém o H1 português {titulo_pt!r}",
                erros,
            )
            href_en = pagina_pt.alternativas.get("en", "")
            exigir(
                bool(href_en)
                and resolver_url(site, pagina_pt_path, href_en) == f"/en/{slug_en}/",
                f"/{slug_pt}/ não alterna para /en/{slug_en}/",
                erros,
            )

    return erros


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python scripts/test-docs-html.py <diretório-site>", file=sys.stderr)
        return 2

    site = Path(sys.argv[1]).resolve()
    erros = validar(site)
    if erros:
        for erro in erros:
            print(f"FALHA: {erro}", file=sys.stderr)
        return 1

    print(f"HTML bilíngue validado: {len(PAGINAS)} pares de páginas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
