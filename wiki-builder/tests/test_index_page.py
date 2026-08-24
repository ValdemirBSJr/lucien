import re
from pathlib import Path

from app.index_page import MARKER, ensure_index


def _publica(docs: Path, caminho: str) -> None:
    alvo = docs / caminho
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("# Runbook\n", encoding="utf-8")


def test_gera_index_quando_o_repositorio_nao_tem(tmp_path: Path) -> None:
    """Sem index.md na raiz o MkDocs nao produz index.html e a release cai."""

    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/servidores/limpar-cache--" + "a" * 8 + "-1111-2222-3333-444455556666.md")

    assert ensure_index(docs) is True

    conteudo = (docs / "index.md").read_text(encoding="utf-8")
    assert conteudo.startswith(MARKER)
    assert "## 2026" in conteudo
    assert "### servidores" in conteudo
    # O UUID sai do texto do link, mas continua no caminho: e ele que
    # identifica o artefato.
    assert "[limpar-cache](" in conteudo
    assert "runbooks/2026/servidores/limpar-cache--" in conteudo


def test_preserva_index_escrito_por_uma_pessoa(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    proprio = "# Capa da equipe\n\nTexto que ninguem deve sobrescrever.\n"
    (docs / "index.md").write_text(proprio, encoding="utf-8")

    assert ensure_index(docs) is False
    assert (docs / "index.md").read_text(encoding="utf-8") == proprio


def test_reescreve_o_proprio_index_a_cada_ciclo(tmp_path: Path) -> None:
    """Sem isto o indice congelaria na primeira geracao.

    O arquivo passa a existir, entao um `if exists(): return` nunca mais o
    atualizaria e runbooks novos ficariam invisiveis.
    """

    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/servidores/primeiro.md")
    ensure_index(docs)

    _publica(docs, "runbooks/2026/acessos/segundo.md")
    assert ensure_index(docs) is True

    conteudo = (docs / "index.md").read_text(encoding="utf-8")
    assert "primeiro" in conteudo
    assert "segundo" in conteudo
    assert "### acessos" in conteudo


def test_reconhece_os_dois_layouts_de_diretorio(tmp_path: Path) -> None:
    """Um repositorio em uso tem os dois: o antigo nao foi movido."""

    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/servidores/novo.md")  # <ano>/<area>
    _publica(docs, "runbooks/servidores/2025/antigo.md")  # <area>/<ano>

    ensure_index(docs)
    conteudo = (docs / "index.md").read_text(encoding="utf-8")

    assert "## 2026" in conteudo
    assert "## 2025" in conteudo
    # Nos dois casos a area foi identificada, nao virou "Sem area".
    assert conteudo.count("### servidores") == 2
    # Ano mais recente primeiro.
    assert conteudo.index("## 2026") < conteudo.index("## 2025")


def test_nome_de_arquivo_nao_injeta_markdown(tmp_path: Path) -> None:
    """O nome vem do repositorio, que e dado nao confiavel para o builder."""

    docs = tmp_path / "docs"
    # `//` no fixture viraria separador de diretorio; o alvo do teste e o nome.
    _publica(docs, "runbooks/2026/servidores/nome[malicioso](x).md")

    ensure_index(docs)
    conteudo = (docs / "index.md").read_text(encoding="utf-8")

    linha = next(
        item for item in conteudo.splitlines() if item.startswith("- [")
    )
    # Uma unica fronteira de link: `]` NAO escapado seguido de `(`. Contar
    # `](` cru daria 2, porque o `\](` do texto casa sem ser fronteira.
    assert len(re.findall(r"(?<!\\)\]\(", linha)) == 1
    # Colchetes escapados no texto...
    assert r"\[malicioso\]" in linha
    # ...e parenteses percent-encoded no destino, que o escape de texto nao
    # cobriria: `)` cru fecharia o link antes da hora.
    assert "%28x%29" in linha


def test_repositorio_vazio_ainda_produz_index(tmp_path: Path) -> None:
    """Um repositorio recem-criado tambem precisa render uma release valida."""

    docs = tmp_path / "docs"
    docs.mkdir(parents=True)

    assert ensure_index(docs) is True
    conteudo = (docs / "index.md").read_text(encoding="utf-8")
    assert "# Runbooks" in conteudo
    assert "Nenhum runbook publicado" in conteudo


def test_ciclo_real_produz_o_index_html_que_o_guard_exige(tmp_path: Path) -> None:
    """A prova que importa: MkDocs real, guard real, no layout do Hub.

    Os testes acima verificam o Markdown gerado. Este verifica a consequência
    que motivou tudo -- `site/index.html` existir -- rodando o mesmo
    MkDocsBuilder e o mesmo validate_site_tree do ciclo de publicação.
    """

    from app.publication import MkDocsBuilder
    from app.tree_guard import validate_site_tree

    docs = tmp_path / "docs"
    _publica(
        docs,
        "runbooks/2026/servidores/reiniciar-servico"
        "--3e381ebe-0284-4d3b-b304-a13655e3dd4c.md",
    )
    site = tmp_path / "site"

    ensure_index(docs)
    MkDocsBuilder(timeout_seconds=120).build(docs, site)

    # Sem o index gerado, esta chamada levantava
    # "o build não produziu um site válido" a cada ciclo, por 5 dias.
    validate_site_tree(site, max_site_bytes=64 * 1024 * 1024)
    assert (site / "index.html").is_file()
    assert (
        site / "runbooks" / "2026" / "servidores"
        / "reiniciar-servico--3e381ebe-0284-4d3b-b304-a13655e3dd4c" / "index.html"
    ).is_file()
    # O link do índice aponta para a página que existe.
    assert "reiniciar-servico" in (site / "index.html").read_text(encoding="utf-8")


def test_falha_do_mkdocs_diz_o_que_aconteceu(tmp_path: Path, caplog) -> None:
    """A mensagem precisa apontar a causa, nao so o codigo de saida.

    Antes, stdout e stderr iam para DEVNULL: o log dizia "status 1" e
    repetia isso a cada ciclo, sem nada por onde comecar.
    """

    import logging

    from app.publication import BuildError, MkDocsBuilder

    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    # `--strict` transforma o link quebrado em erro de build.
    (docs / "index.md").write_text(
        "# Capa\n\n[apontando para o vazio](nao-existe.md)\n", encoding="utf-8"
    )

    with caplog.at_level(logging.ERROR, logger="lucien.wiki_builder"):
        try:
            MkDocsBuilder(timeout_seconds=120).build(docs, tmp_path / "site")
        except BuildError as erro:
            mensagem = str(erro)
        else:
            raise AssertionError("build com link quebrado deveria falhar")

    # A exceção carrega o status e um resumo de uma linha, que é o que
    # aparece na mensagem do ciclo.
    assert "status" in mensagem
    assert mensagem != "o MkDocs encerrou com status 1"
    # E o log traz o bloco com a saída real do MkDocs.
    detalhe = "\n".join(registro.getMessage() for registro in caplog.records)
    assert "MkDocs falhou" in detalhe
    assert "nao-existe.md" in detalhe


def test_area_declarada_e_ainda_vazia_aparece(tmp_path: Path) -> None:
    """Uma area recem-criada e invisivel ate a primeira publicacao.

    Ela ja existe no .env e `lucien start -r` a aceita, mas nao tem
    diretorio no repositorio -- entao a descoberta por disco nao a encontra.
    """

    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/servidores/existente.md")

    ensure_index(docs, ("acessos", "servidores", "roteamento"))
    conteudo = (docs / "index.md").read_text(encoding="utf-8")

    assert "- **roteamento** — nenhum runbook publicado ainda" in conteudo
    assert "- **acessos** — nenhum runbook publicado ainda" in conteudo
    assert "- **servidores** — 1 runbook" in conteudo
    # A listagem cronologica nao inventa secao para area vazia.
    assert "### roteamento" not in conteudo


def test_area_fora_da_lista_nao_some_do_indice(tmp_path: Path) -> None:
    """Renomear uma area no .env nao pode esconder o que ja foi publicado."""

    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/legado/antigo.md")

    ensure_index(docs, ("servidores",))
    conteudo = (docs / "index.md").read_text(encoding="utf-8")

    assert "- **legado** — 1 runbook" in conteudo
    assert "### legado" in conteudo
    assert "antigo" in conteudo


def test_plural_das_contagens(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/servidores/um.md")
    _publica(docs, "runbooks/2026/servidores/dois.md")
    _publica(docs, "runbooks/2026/acessos/unico.md")

    ensure_index(docs, ())
    conteudo = (docs / "index.md").read_text(encoding="utf-8")

    assert "- **servidores** — 2 runbooks" in conteudo
    assert "- **acessos** — 1 runbook" in conteudo
    assert "1 runbooks" not in conteudo


def test_sem_areas_declaradas_o_indice_segue_funcionando(tmp_path: Path) -> None:
    """RUNBOOK_DOMAIN_FUNCTIONS ausente no builder nao pode quebrar nada."""

    docs = tmp_path / "docs"
    _publica(docs, "runbooks/2026/servidores/existente.md")

    assert ensure_index(docs) is True
    conteudo = (docs / "index.md").read_text(encoding="utf-8")
    assert "- **servidores** — 1 runbook" in conteudo


def test_settings_le_as_areas_declaradas() -> None:
    from app.settings import _domain_functions

    assert _domain_functions(
        {"RUNBOOK_DOMAIN_FUNCTIONS": "acessos, servidores ,roteamento"}
    ) == ("acessos", "servidores", "roteamento")
    # Entrada malformada e ignorada em vez de derrubar o builder: quem valida
    # essa variavel a serio e o Hub, que a usa para autorizar.
    # `Acessos` tem maiuscula, `ok` tem 2 caracteres e o minimo e 3, `x` idem:
    # a gramatica e a mesma do Hub, entao so `redes` sobrevive.
    assert _domain_functions(
        {"RUNBOOK_DOMAIN_FUNCTIONS": "Acessos,ok,,redes,x"}
    ) == ("redes",)
    assert _domain_functions({}) == ()
