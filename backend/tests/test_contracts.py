"""Golden files do que o Hub produz para os seus consumidores.

O portal e o Hub têm ambos um pacote `app` e não podem ser importados no mesmo
processo. Estes arquivos são o que os obriga a concordar: aqui eles são
regenerados a partir do código real e comparados; em
`runbook-viewer/tests/test_contracts.py` eles são lidos e validados pelos
schemas do portal.

Uma falha aqui significa que o Hub mudou a forma de algo publicado. Isso é
legítimo -- basta aceitar o arquivo novo -- mas a mudança precisa ser
consciente, porque do outro lado alguém depende dela.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.api.schemas import UserResponse
from app.domain.models import (
    Job,
    JobStatus,
    PublicationIdentity,
    RoleLevel,
    RunbookSuggestions,
    User,
)
from app.domain.publication import (
    build_frontmatter,
    build_revision_frontmatter,
    validate_playbook,
)

CONTRATOS = Path(__file__).resolve().parent.parent / "contracts"

_UTC = timezone.utc
_ID_PUBLICADO = "3e381ebe-0284-4d3b-b304-a13655e3dd4c"
_ID_RAIZ = "52d1b673-06f4-45ac-96db-73a5a9cf11c0"
_ID_ANTERIOR = "a1b2c3d4-1111-2222-3333-444455556666"

_CORPO = (
    "### Passo 1: Inspecionar rota\n"
    "```bash\n"
    "ip route show\n"
    "```\n"
    "> Explique o objetivo, o impacto e o resultado esperado.\n"
)


def _usuario() -> User:
    """Usuário com tudo que o contrato precisa exercitar.

    Nome do LDAP e áreas adicionais são justamente os campos que quebraram o
    portal quando foram acrescentados sem atualizar o outro lado.
    """

    return User(
        id="11111111-1111-4111-8111-111111111111",
        username="U000004",
        role_level=RoleLevel.SENIOR,
        domain_function="servidores",
        is_active=True,
        display_name="Operador Exemplo de Demonstracao Júnior",
        extra_domains=("acessos", "redes"),
    )


def _job(job_id: str, **extras: object) -> Job:
    return Job(
        id=job_id,
        owner_id="11111111-1111-4111-8111-111111111111",
        name="consulta-resolucao-dns",
        status=JobStatus.PENDING,
        commands=("ip route show",),
        command_outputs=("default via 10.0.0.254 dev eth0",),
        runbook_suggestions=RunbookSuggestions("", (), ("",), ()),
        inferred_tags=("rede", "criticidade_baixa"),
        created_at=datetime(2026, 8, 20, 18, 15, 5, 409161, tzinfo=_UTC),
        **extras,  # type: ignore[arg-type]
    )


def _identidade() -> PublicationIdentity:
    return PublicationIdentity(
        username="U000004",
        role_level=RoleLevel.SENIOR,
        domain_function="servidores",
        display_name="Operador Exemplo de Demonstracao Júnior",
    )


def _confere(nome: str, produzido: str) -> None:
    """Compara com o golden e explica o que fazer quando divergir."""

    arquivo = CONTRATOS / nome
    if not arquivo.exists():
        arquivo.write_text(produzido, encoding="utf-8")
        raise AssertionError(
            f"contrato {nome} criado agora; revise o conteúdo e faça commit"
        )
    atual = arquivo.read_text(encoding="utf-8")
    if atual != produzido:
        arquivo.write_text(produzido, encoding="utf-8")
        raise AssertionError(
            f"o Hub mudou a forma de {nome}. O arquivo foi atualizado: confira o "
            "diff, faça commit e verifique se o portal ainda consegue ler."
        )


def test_contrato_resposta_de_usuario() -> None:
    """`GET /me` é o que o portal usa para decidir o que exibir."""

    payload = UserResponse.from_domain(_usuario()).model_dump(mode="json")
    _confere(
        "me_response.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def test_contrato_frontmatter_publicado() -> None:
    validado = validate_playbook(_CORPO)
    documento = build_frontmatter(_job(_ID_PUBLICADO), _identidade(), validado)
    _confere("frontmatter_publicado.md", documento)


def test_contrato_frontmatter_revisao() -> None:
    validado = validate_playbook(_CORPO)
    job = _job(
        _ID_PUBLICADO,
        root_job_id=_ID_RAIZ,
        supersedes_job_id=_ID_ANTERIOR,
        revision_number=2,
    )
    documento = build_revision_frontmatter(job, _identidade(), validado)
    _confere("frontmatter_revisao.md", documento)


def test_contrato_frontmatter_sem_nome_do_ldap() -> None:
    """Usuário criado pelo admin não tem nome; o autor cai para o username."""

    identidade = PublicationIdentity(
        username="U000004",
        role_level=RoleLevel.SENIOR,
        domain_function="servidores",
    )
    validado = validate_playbook(_CORPO)
    documento = build_frontmatter(_job(_ID_PUBLICADO), identidade, validado)
    _confere("frontmatter_sem_display_name.md", documento)
