import os
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.repository import CatalogLimitError, RunbookRepository


def _write_runbook(
    root: Path,
    runbook_id: str,
    body: str = "# Reiniciar serviço\n\n### Passo 1: Reiniciar\n```bash\nsystemctl restart app\n```\n",
    *,
    extra_metadata: str = "",
) -> Path:
    target = root / "servidores" / "2026" / f"{runbook_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        f'id: "{runbook_id}"\n'
        'autor: "operador"\n'
        'nivel_autor: "senior"\n'
        'funcao: "servidores"\n'
        'data_criacao: "2026-07-22T18:00:00Z"\n'
        'tags_inferidas: ["linux", "systemd"]\n'
        f"{extra_metadata}"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )
    return target


def _write_revision(
    root: Path,
    root_id: str,
    revision_id: str,
    replaces: str,
    revision: int,
    body: str,
    domain: str = "servidores",
) -> Path:
    target = root / "servidores" / "2026" / f"{revision_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        f'id: "{revision_id}"\n'
        'autor: "admin"\n'
        'nivel_autor: "admin"\n'
        f'funcao: "{domain}"\n'
        'data_criacao: "2026-07-23T18:00:00Z"\n'
        'tags_inferidas: ["linux", "systemd"]\n'
        f'runbook_raiz: "{root_id}"\n'
        f"revisao: {revision}\n"
        f'substitui: "{replaces}"\n'
        "---\n"
        f"{body}",
        encoding="utf-8",
    )
    return target


def _all_ids(root: Path) -> frozenset[str]:
    return frozenset(path.stem for path in root.rglob("*.md"))


@pytest.mark.asyncio
async def test_indexa_formato_local_e_renderiza_markdown_seguro(tmp_path: Path) -> None:
    runbook_id = str(uuid4())
    _write_runbook(
        tmp_path,
        runbook_id,
        "# Reiniciar API\n\n<script>alert(1)</script>\n"
        "[link](javascript:alert(2))\n\n```bash\nsystemctl restart api\n```\n",
    )
    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=60)

    published = frozenset({runbook_id})
    summaries = await repository.list_runbooks(published)
    document = await repository.get_runbook(runbook_id, published)

    assert len(summaries) == 1
    assert summaries[0].title == "Reiniciar API"
    assert summaries[0].domain_function == "servidores"
    assert document is not None
    assert "<script" not in document.html
    # O parser pode preservar o esquema perigoso como texto, mas nunca como link.
    assert 'href="javascript:' not in document.html
    assert "language-bash" in document.html


@pytest.mark.asyncio
async def test_ignora_symlinks_e_arquivos_fora_da_arvore_contratada(
    tmp_path: Path,
) -> None:
    valid_id = str(uuid4())
    outside_id = str(uuid4())
    valid = _write_runbook(tmp_path, valid_id)
    outside = _write_runbook(tmp_path / "outside", outside_id)
    linked_id = str(uuid4())
    os.symlink(
        outside,
        tmp_path / "servidores" / "2026" / f"{linked_id}.md",
    )
    os.symlink(valid.parent, tmp_path / "servidores" / "2027")
    _write_runbook(tmp_path / "wrong", str(uuid4()))

    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=0)
    summaries = await repository.list_runbooks(frozenset({valid_id}))

    assert [summary.id for summary in summaries] == [valid_id]


@pytest.mark.asyncio
async def test_aceita_nome_legivel_com_uuid_e_formato_legado(tmp_path: Path) -> None:
    named_id = str(uuid4())
    named = _write_runbook(tmp_path, named_id)
    named.rename(named.with_name(f"reiniciar-api--{named_id}.md"))

    legacy_id = str(uuid4())
    legacy = _write_runbook(tmp_path, legacy_id)
    legacy_target = tmp_path / "2026" / "07" / legacy.name
    legacy_target.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(legacy_target)

    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=0)
    summaries = await repository.list_runbooks(frozenset({named_id, legacy_id}))

    assert {summary.id for summary in summaries} == {named_id, legacy_id}


@pytest.mark.asyncio
async def test_rejeita_frontmatter_extra_id_divergente_e_arquivo_grande(
    tmp_path: Path,
) -> None:
    _write_runbook(tmp_path, str(uuid4()), extra_metadata='admin: true\n')
    divergent_id = str(uuid4())
    divergent = _write_runbook(tmp_path, divergent_id)
    divergent.write_text(
        divergent.read_text(encoding="utf-8").replace(divergent_id, str(uuid4()), 1),
        encoding="utf-8",
    )
    large_id = str(uuid4())
    _write_runbook(tmp_path, large_id, "# Título\n" + ("x" * 3000))

    repository = RunbookRepository(tmp_path, 10, 2048, cache_ttl_seconds=0)
    assert await repository.list_runbooks(_all_ids(tmp_path)) == ()


@pytest.mark.asyncio
async def test_falha_fechada_quando_limite_de_documentos_e_excedido(
    tmp_path: Path,
) -> None:
    _write_runbook(tmp_path, str(uuid4()))
    _write_runbook(tmp_path, str(uuid4()))
    repository = RunbookRepository(tmp_path, 1, 1024 * 1024, cache_ttl_seconds=0)

    with pytest.raises(CatalogLimitError):
        await repository.list_runbooks(_all_ids(tmp_path))


@pytest.mark.asyncio
async def test_acesso_aceita_apenas_uuid_canonico(tmp_path: Path) -> None:
    runbook_id = str(uuid4())
    _write_runbook(tmp_path, runbook_id)
    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=60)

    published = frozenset({runbook_id})
    assert await repository.get_runbook(runbook_id, published) is not None
    assert await repository.get_runbook("../../etc/passwd", published) is None
    assert await repository.get_runbook(runbook_id.upper(), published) is None


@pytest.mark.asyncio
async def test_agrupa_revisoes_e_expoe_corpo_hash_pela_raiz(tmp_path: Path) -> None:
    root_id = str(uuid4())
    revision_id = str(uuid4())
    _write_runbook(tmp_path, root_id, "# Versão inicial\n\nTexto.\n")
    body = "# Versão revisada\n\n### Passo 1: Validar\n```bash\nid\n```\n"
    _write_revision(tmp_path, root_id, revision_id, root_id, 2, body)
    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=60)

    published = frozenset({root_id, revision_id})
    summaries = await repository.list_runbooks(published)
    document = await repository.get_runbook(root_id, published)

    assert len(summaries) == 1
    assert summaries[0].id == revision_id
    assert summaries[0].root_id == root_id
    assert summaries[0].revision == 2
    assert document is not None
    assert document.markdown == body
    assert document.body_hash == hashlib.sha256(body.encode()).hexdigest()
    assert await repository.get_runbook(revision_id, published) is None


@pytest.mark.asyncio
async def test_interrompe_cadeia_de_revisao_quebrada(tmp_path: Path) -> None:
    root_id = str(uuid4())
    _write_runbook(tmp_path, root_id)
    _write_revision(
        tmp_path,
        root_id,
        str(uuid4()),
        str(uuid4()),
        2,
        "# Revisão sem ancestral correto\n",
    )
    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=60)

    summaries = await repository.list_runbooks(_all_ids(tmp_path))

    assert len(summaries) == 1
    assert summaries[0].id == root_id
    assert summaries[0].revision == 1


@pytest.mark.asyncio
async def test_filtra_pending_e_preserva_dominio_da_publicacao_raiz(
    tmp_path: Path,
) -> None:
    root_id = str(uuid4())
    revision_id = str(uuid4())
    _write_runbook(tmp_path, root_id, "# Versão publicada\n")
    _write_revision(
        tmp_path,
        root_id,
        revision_id,
        root_id,
        2,
        "# Revisão administrativa\n",
        domain="plataforma",
    )
    repository = RunbookRepository(tmp_path, 10, 1024 * 1024, cache_ttl_seconds=60)

    before_publish = await repository.list_runbooks(frozenset({root_id}))
    after_publish = await repository.list_runbooks(
        frozenset({root_id, revision_id})
    )
    document = await repository.get_runbook(
        root_id, frozenset({root_id, revision_id})
    )

    assert before_publish[0].id == root_id
    assert after_publish[0].id == revision_id
    assert after_publish[0].domain_function == "plataforma"
    assert after_publish[0].root_domain_function == "servidores"
    assert document is not None
    assert document.summary.root_domain_function == "servidores"


@pytest.mark.asyncio
async def test_arquivos_nao_publicados_nao_consumem_limite_do_catalogo(
    tmp_path: Path,
) -> None:
    published_id = str(uuid4())
    _write_runbook(tmp_path, published_id)
    for _ in range(5):
        _write_runbook(tmp_path, str(uuid4()))
    repository = RunbookRepository(tmp_path, 1, 1024 * 1024, cache_ttl_seconds=0)

    summaries = await repository.list_runbooks(frozenset({published_id}))

    assert [summary.id for summary in summaries] == [published_id]
