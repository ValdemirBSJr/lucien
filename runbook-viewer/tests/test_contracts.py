"""O portal precisa ler o que o Hub produz de verdade.

Os arquivos em `backend/contracts/` são regenerados pelo próprio Hub a partir
do código real. Aqui eles passam pelos schemas do portal. Uma falha significa
que o Hub produz algo que o portal não aceita -- foi exatamente o que
aconteceu quando `display_name` e `extra_domains` entraram no `/me` sem que
ninguém tocasse neste lado.

Ler arquivo em vez de importar o Hub é deliberado: os dois serviços têm um
pacote chamado `app` e não convivem no mesmo processo.
"""

import json
from pathlib import Path

import pytest

def _localiza_contratos() -> Path:
    """No repositorio os contratos ficam dois niveis acima; na imagem, ao lado.

    Procurar nos dois evita duplicar os golden files so para satisfazer o
    layout do contêiner.
    """

    aqui = Path(__file__).resolve()
    for candidato in (
        aqui.parents[2] / "backend" / "contracts",
        aqui.parents[1] / "backend" / "contracts",
    ):
        if candidato.is_dir():
            return candidato
    return aqui.parents[1] / "backend" / "contracts"


CONTRATOS = _localiza_contratos()

pytestmark = pytest.mark.skipif(
    not CONTRATOS.is_dir(),
    reason="contratos do Hub ausentes; rode a partir da raiz do repositório",
)


def _carrega(nome: str) -> str:
    return (CONTRATOS / nome).read_text(encoding="utf-8")


def test_resposta_de_usuario_do_hub_e_aceita() -> None:
    """A resposta real de `GET /me`, com nome do LDAP e áreas adicionais."""

    from app.security import _MePayload

    payload = json.loads(_carrega("me_response.json"))
    usuario = _MePayload.model_validate(payload)

    assert usuario.username == "U000004"
    assert usuario.domain_function == "servidores"
    assert usuario.display_name == "Operador Exemplo de Demonstracao Júnior"
    assert list(usuario.extra_domains) == ["acessos", "redes"]


def test_runbook_publicado_pelo_hub_e_legivel(tmp_path: Path) -> None:
    resumo = _analisa(tmp_path, "frontmatter_publicado.md")

    assert resumo.domain_function == "servidores"
    assert resumo.author_level == "senior"
    # O autor traz o nome do LDAP; ele é apresentação, não autorização.
    assert "Operador Exemplo de Demonstracao Júnior" in resumo.author
    assert "U000004" in resumo.author


def test_revisao_publicada_pelo_hub_e_legivel(tmp_path: Path) -> None:
    resumo = _analisa(tmp_path, "frontmatter_revisao.md")

    assert resumo.domain_function == "servidores"
    assert resumo.revision == 2
    assert resumo.root_id == "52d1b673-06f4-45ac-96db-73a5a9cf11c0"
    assert resumo.replaces == "a1b2c3d4-1111-2222-3333-444455556666"


def test_runbook_sem_nome_do_ldap_e_legivel(tmp_path: Path) -> None:
    """Usuário criado pelo admin publica com o username sozinho."""

    resumo = _analisa(tmp_path, "frontmatter_sem_display_name.md")

    assert resumo.author == "U000004"


def _analisa(raiz: Path, nome: str):
    """Grava o artefato no layout atual do Hub e passa pelo parser do portal."""

    from app.repository import RunbookRepository

    documento = _carrega(nome)
    destino = raiz / "2026" / "servidores"
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / (
        "consulta-resolucao-dns--3e381ebe-0284-4d3b-b304-a13655e3dd4c.md"
    )
    caminho.write_text(documento, encoding="utf-8")
    resumo, _corpo = RunbookRepository._parse_document(caminho, documento)
    return resumo


def test_area_adicional_habilita_a_edicao() -> None:
    """Um operador atende mais de uma área; o botão precisa refletir isso.

    Comparar só com a área primária esconderia a edição de quem o Hub
    autorizaria. O caminho inverso é inofensivo: quem não pode recebe 403 do
    Hub, que continua sendo a decisão final.
    """

    from app.main import _can_edit
    from app.models import AuthenticatedUser

    operador = AuthenticatedUser(
        id="11111111-1111-4111-8111-111111111111",
        username="U000004",
        role_level="senior",
        domain_function="servidores",
        extra_domains=("acessos",),
    )

    assert _can_edit(operador, "servidores") is True
    assert _can_edit(operador, "acessos") is True
    # Área que ele não tem continua fechada.
    assert _can_edit(operador, "redes") is False


def test_area_adicional_malformada_recusa_a_identidade() -> None:
    """As áreas decidem edição, então passam pela mesma gramática da primária."""

    from pydantic import ValidationError

    from app.security import _MePayload

    base = json.loads(_carrega("me_response.json"))

    # Campo desconhecido continua recusado: foi ele que acusou a divergência.
    with pytest.raises(ValidationError):
        _MePayload.model_validate({**base, "campo_novo": "x"})


def test_autor_com_controle_e_recusado(tmp_path: Path) -> None:
    """O autor sai num cabeçalho e numa página; quebra de linha não passa."""

    from app.repository import _valid_author_label

    assert _valid_author_label("U000004 - Operador Exemplo de Demonstracao Júnior")
    assert _valid_author_label("U000004")
    assert not _valid_author_label("U000004\nautor: admin")
    assert not _valid_author_label("U000004\x07")
    assert not _valid_author_label(" U000004 ")
    assert not _valid_author_label("")
    assert not _valid_author_label("x" * 201)


def test_schema_desconhecido_continua_recusado(tmp_path: Path) -> None:
    """A comparação exata de chaves é a rede que acusou os P0.

    Aceitar qualquer conjunto trocaria uma falha visível por uma silenciosa.
    """

    from app.repository import RunbookRepository

    documento = _carrega("frontmatter_publicado.md").replace(
        'versao: "1"', 'versao: "1"\ncampo_inesperado: "x"'
    )
    caminho = tmp_path / "consulta--3e381ebe-0284-4d3b-b304-a13655e3dd4c.md"
    caminho.write_text(documento, encoding="utf-8")

    with pytest.raises(ValueError, match="schema de frontmatter inválido"):
        RunbookRepository._parse_document(caminho, documento)
