"""Hook fixo que remove HTML ativo do conteúdo renderizado pelo Markdown."""

from __future__ import annotations

import bleach

_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "input",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "summary",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

_ALLOWED_ATTRIBUTES = {
    "*": ["class", "id"],
    "a": ["href", "title"],
    "img": ["alt", "height", "src", "title", "width"],
    "input": ["checked", "disabled", "type"],
}


def sanitize_page_content(html: str) -> str:
    """Preserva a semântica dos runbooks e remove scripts e atributos ativos."""

    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        strip=True,
        strip_comments=True,
    )


def on_page_content(html: str, **_: object) -> str:
    """Ponto de extensão MkDocs carregado somente da imagem confiável."""

    return sanitize_page_content(html)

