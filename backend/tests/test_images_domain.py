import pytest

from app.domain.images import (
    extract_asset_references,
    previously_published_asset_paths,
    rewritten_markdown,
    validate_asset_completeness,
)
from app.domain.ports import ValidationError

_JOB_ID = "1e6a4d1a-9b3e-4c9a-8b0e-2f7a6c9d0e11"
_OTHER_JOB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_extracts_valid_reference() -> None:
    markdown = f"texto\n![print da tela](assets/{_JOB_ID}/shot.png)\nfim"
    references = extract_asset_references(markdown, _JOB_ID)
    assert len(references) == 1
    assert references[0].alt == "print da tela"
    assert references[0].filename == "shot.png"


def test_extracts_multiple_references_in_order() -> None:
    markdown = (
        f"![primeiro](assets/{_JOB_ID}/a.png)\n"
        f"![segundo](assets/{_JOB_ID}/b.jpg)"
    )
    references = extract_asset_references(markdown, _JOB_ID)
    assert [reference.filename for reference in references] == ["a.png", "b.jpg"]


def test_empty_caption_is_rejected() -> None:
    markdown = f"![](assets/{_JOB_ID}/shot.png)"
    with pytest.raises(ValidationError, match="caption"):
        extract_asset_references(markdown, _JOB_ID)


def test_whitespace_only_caption_is_rejected() -> None:
    markdown = f"![   ](assets/{_JOB_ID}/shot.png)"
    with pytest.raises(ValidationError, match="caption"):
        extract_asset_references(markdown, _JOB_ID)


def test_reference_to_another_job_is_rejected() -> None:
    markdown = f"![print](assets/{_OTHER_JOB_ID}/shot.png)"
    with pytest.raises(ValidationError, match="different job"):
        extract_asset_references(markdown, _JOB_ID)


def test_external_url_is_rejected() -> None:
    markdown = "![print](https://evil.example/leak.png)"
    with pytest.raises(ValidationError, match="assets/<job_id>"):
        extract_asset_references(markdown, _JOB_ID)


def test_path_traversal_is_rejected() -> None:
    markdown = f"![print](assets/{_JOB_ID}/../../etc/passwd)"
    with pytest.raises(ValidationError, match="assets/<job_id>"):
        extract_asset_references(markdown, _JOB_ID)


def test_no_reference_returns_empty_tuple() -> None:
    assert extract_asset_references("apenas texto, sem imagem", _JOB_ID) == ()


def test_completeness_accepts_matching_sets() -> None:
    references = extract_asset_references(
        f"![print](assets/{_JOB_ID}/shot.png)", _JOB_ID
    )
    validate_asset_completeness(references, frozenset({"shot.png"}))


def test_completeness_rejects_orphan_reference() -> None:
    references = extract_asset_references(
        f"![print](assets/{_JOB_ID}/shot.png)", _JOB_ID
    )
    with pytest.raises(ValidationError, match="not submitted"):
        validate_asset_completeness(references, frozenset())


def test_completeness_rejects_unreferenced_asset() -> None:
    with pytest.raises(ValidationError, match="not referenced"):
        validate_asset_completeness((), frozenset({"shot.png"}))


def test_rewritten_markdown_replaces_filename_and_keeps_caption() -> None:
    markdown = f"antes ![print da tela](assets/{_JOB_ID}/shot.png) depois"
    result = rewritten_markdown(markdown, _JOB_ID, {"shot.png": "3f9ac1.png"})
    assert result == f"antes ![print da tela](assets/{_JOB_ID}/3f9ac1.png) depois"


def test_reference_to_ancestor_job_is_accepted_when_already_existing() -> None:
    """Numa revisao-de-revisao, uma imagem herdada aponta pro avo, nao pro pai."""
    raw_path = f"assets/{_OTHER_JOB_ID}/shot.png"
    markdown = f"![print](assets/{_OTHER_JOB_ID}/shot.png)"
    references = extract_asset_references(
        markdown, _JOB_ID, already_existing_paths=frozenset({raw_path})
    )
    assert references[0].filename == "shot.png"
    assert references[0].path == raw_path


def test_reference_to_ancestor_job_still_rejected_when_not_already_existing() -> None:
    markdown = f"![print](assets/{_OTHER_JOB_ID}/shot.png)"
    with pytest.raises(ValidationError, match="different job"):
        extract_asset_references(markdown, _JOB_ID)


def test_completeness_exempts_already_existing_reference_from_submission() -> None:
    raw_path = f"assets/{_JOB_ID}/shot.png"
    references = extract_asset_references(
        f"![print](assets/{_JOB_ID}/shot.png)",
        _JOB_ID,
        already_existing_paths=frozenset({raw_path}),
    )
    # Nada foi submetido de novo -- a imagem herdada nao exige reenvio.
    validate_asset_completeness(references, frozenset(), frozenset({raw_path}))


def test_completeness_still_requires_submission_for_a_genuinely_new_reference() -> None:
    existing_path = f"assets/{_JOB_ID}/old.png"
    references = extract_asset_references(
        f"![velho](assets/{_JOB_ID}/old.png)\n![novo](assets/{_JOB_ID}/new.png)",
        _JOB_ID,
        already_existing_paths=frozenset({existing_path}),
    )
    with pytest.raises(ValidationError, match="not submitted"):
        validate_asset_completeness(references, frozenset(), frozenset({existing_path}))
    # Com o novo enviado, passa -- o antigo continua isento.
    validate_asset_completeness(
        references, frozenset({"new.png"}), frozenset({existing_path})
    )


def test_previously_published_asset_paths_collects_well_formed_references() -> None:
    markdown = (
        f"![um](assets/{_JOB_ID}/a.png) "
        f"![dois](assets/{_OTHER_JOB_ID}/b.png) "
        "![externo](https://evil.example/leak.png)"
    )
    paths = previously_published_asset_paths(markdown)
    assert paths == {
        f"assets/{_JOB_ID}/a.png",
        f"assets/{_OTHER_JOB_ID}/b.png",
    }


def test_rewritten_markdown_handles_multiple_references() -> None:
    markdown = (
        f"![um](assets/{_JOB_ID}/a.png) e ![dois](assets/{_JOB_ID}/b.png)"
    )
    result = rewritten_markdown(
        markdown, _JOB_ID, {"a.png": "opaque-a.png", "b.png": "opaque-b.png"}
    )
    assert result == (
        f"![um](assets/{_JOB_ID}/opaque-a.png) e "
        f"![dois](assets/{_JOB_ID}/opaque-b.png)"
    )
