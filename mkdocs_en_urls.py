"""URLs em ingles para as paginas de docs/en, sem renomear arquivo nenhum.

O mkdocs-static-i18n pareia os dois idiomas pelo NOME DO ARQUIVO. Renomear
`docs/en/publicacao.md` para `publication.md` quebra o par em silencio: a
pagina inglesa fica orfa, `/en/publicacao/` passa a servir portugues por
`fallback_to_default`, e `mkdocs build --strict` continua aprovando.

Este hook nao toca em nome de arquivo. Ele reescreve apenas o destino
publicado, depois que o plugin ja montou os pares -- entao o pareamento
continua valendo e so a URL muda.

O portugues fica na raiz e nao e afetado: so entradas sob `en/` sao
reescritas.
"""

from __future__ import annotations

# Somente os nomes que realmente diferem. `tutorial`, `iam-rbac`, `index` e
# `runbooks` ja sao iguais nos dois idiomas e nao entram aqui.
TRADUCAO = {
    "documentacao-tecnica": "technical-documentation",
    "implantacao-isolada": "isolated-deployment",
    "manual-instalacao": "installation-manual",
    "operacao": "operations",
    "publicacao": "publication",
}


def on_files(files, config):
    """Roda depois do plugin de i18n, sobre os arquivos ja pareados."""
    for arquivo in files:
        destino = arquivo.dest_uri
        if not destino.startswith("en/"):
            continue
        partes = destino.split("/")
        # `en/<nome>/index.html` com use_directory_urls, ou `en/<nome>.html`.
        if len(partes) >= 2:
            alvo = partes[1]
            base, ponto, extensao = alvo.partition(".")
            if base in TRADUCAO:
                partes[1] = TRADUCAO[base] + ponto + extensao
                arquivo.dest_uri = "/".join(partes)
    return files
