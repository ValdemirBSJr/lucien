"""O nome do arquivo de revisão, que é lido por gente.

O esquema anterior era `revision-<uuid-da-raiz>-r2--<uuid-novo>.md`: exato e
ilegível. Quem abria o repositório não conseguia dizer de que runbook aquela
revisão era. O nome passa a ser o da raiz mais `-version-<n>`.
"""

from datetime import datetime, timezone

import pytest

from app.domain.ports import ConflictError
from app.infrastructure.storage import (
    playbook_relative_path,
    revision_artifact_name,
)

RAIZ = "06de3bcc-5eff-42c6-8048-dff51e85db64"


def test_nome_da_revisao_diz_de_qual_runbook_e() -> None:
    assert (
        revision_artifact_name("rotina-seguranca-jump-lucien", 2, RAIZ)
        == "rotina-seguranca-jump-lucien-version-2"
    )


def test_revisao_da_revisao_nao_encadeia_o_sufixo() -> None:
    """A base é sempre a raiz, nunca o antecessor imediato.

    A revisão 3 nasce da 2. Encadear o nome do antecessor daria
    `rotina-...-version-2-version-3`.
    """
    assert (
        revision_artifact_name("rotina-seguranca-jump-lucien", 3, RAIZ)
        == "rotina-seguranca-jump-lucien-version-3"
    )


def test_sufixo_de_sessao_do_cli_nao_entra_no_nome() -> None:
    assert (
        revision_artifact_name("coleta-switch-20260814-191422-9f3ac1b20e77", 2, RAIZ)
        == "coleta-switch-version-2"
    )


def test_nome_longo_cabe_na_coluna() -> None:
    """`JobRow.name` é String(80): passar disso quebraria a gravação depois de
    todo o trabalho de revisão já feito."""
    nome = revision_artifact_name("x" * 120, 12, RAIZ)
    assert len(nome) <= 80
    assert nome.endswith("-version-12")


def test_truncagem_nao_deixa_hifen_solto() -> None:
    base = "a" * 66 + "-corte"
    assert "--version-" not in revision_artifact_name(base, 2, RAIZ)


@pytest.mark.parametrize("raiz_invalida", [None, "", "nome com espaço", "../fuga"])
def test_raiz_inutilizavel_cai_no_esquema_por_uuid(raiz_invalida) -> None:
    """Uma revisão não pode ser recusada por causa do nome do documento de
    origem. O esquema por UUID não é bonito, mas nunca falha."""
    assert (
        revision_artifact_name(raiz_invalida, 2, RAIZ)
        == f"revision-{RAIZ}-r2"
    )


def test_caminho_publicado_junta_nome_legivel_e_identidade() -> None:
    """O UUID continua no arquivo: é ele que garante unicidade no disco."""
    caminho = playbook_relative_path(
        "17349111-2a12-44b8-ae9c-4f526cd3374a",
        datetime(2026, 8, 21, tzinfo=timezone.utc),
        artifact_name="rotina-seguranca-jump-lucien-version-2",
        domain_function="servidores",
    )
    assert caminho.as_posix() == (
        "2026/servidores/"
        "rotina-seguranca-jump-lucien-version-2"
        "--17349111-2a12-44b8-ae9c-4f526cd3374a.md"
    )


def test_revisao_ja_publicada_continua_resolvendo_para_o_arquivo_dela() -> None:
    """Nada precisa ser migrado no disco.

    O caminho é derivado do nome gravado na linha do Job, e as revisões
    antigas mantêm o nome com que nasceram. Elas continuam sendo encontradas
    onde estão, sem código de compatibilidade.
    """
    antigo = f"revision-{RAIZ}-r2"
    with pytest.raises(ConflictError):
        # Prova que o nome antigo não é inválido por acidente: ele passa pelas
        # mesmas regras de nome de artefato.
        playbook_relative_path("não-é-uuid", datetime.now(timezone.utc))

    caminho = playbook_relative_path(
        "17349111-2a12-44b8-ae9c-4f526cd3374a",
        datetime(2026, 8, 14, tzinfo=timezone.utc),
        artifact_name=antigo,
        domain_function="servidores",
    )
    assert caminho.name == (
        f"revision-{RAIZ}-r2--17349111-2a12-44b8-ae9c-4f526cd3374a.md"
    )
