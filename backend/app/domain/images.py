import re
from dataclasses import dataclass

from app.domain.ports import ValidationError

# So aceitamos `assets/<job_id>/<arquivo>`: qualquer outra forma (URL externa,
# `../`, asset de outro job) e recusada em vez de tratada como texto inerte.
# Uma imagem que escapasse dessa gramatica contornaria inteiramente o gate de
# OCR/segredo, porque nunca seria enviada como asset nem varrida.
_ASSET_REFERENCE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\((?P<path>[^)\s]+)\)")
_ASSET_PATH = re.compile(
    r"^assets/(?P<job_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/(?P<filename>[A-Za-z0-9._-]{1,128})$"
)


@dataclass(frozen=True, slots=True)
class AssetReference:
    alt: str
    filename: str
    raw: str


def extract_asset_references(
    markdown: str, expected_job_id: str
) -> tuple[AssetReference, ...]:
    """Acha toda imagem Markdown e garante que pertence a este job.

    A legenda (alt text) e obrigatoria: e a ultima barreira humana contra
    vazamento, alem do OCR -- nunca em lugar dele.
    """

    references: list[AssetReference] = []
    for match in _ASSET_REFERENCE.finditer(markdown):
        alt = match.group("alt").strip()
        if not alt:
            raise ValidationError(
                "every image needs a non-empty caption (alt text); "
                "the operator is the last line of defense against leaks"
            )
        path_match = _ASSET_PATH.fullmatch(match.group("path"))
        if path_match is None:
            raise ValidationError(
                f"image reference '{match.group('path')}' must point to "
                "assets/<job_id>/<filename>"
            )
        if path_match.group("job_id") != expected_job_id:
            raise ValidationError("image reference points to a different job's assets")
        references.append(
            AssetReference(
                alt=alt, filename=path_match.group("filename"), raw=match.group(0)
            )
        )
    return tuple(references)


def validate_asset_completeness(
    references: tuple[AssetReference, ...],
    submitted_filenames: frozenset[str],
) -> None:
    """Checagem 1:1: toda referencia tem asset enviado e vice-versa.

    Sem isso, uma referencia orfa publicaria um link quebrado, e um asset
    nao referenciado ficaria escondido no repositorio sem necessidade.
    """

    referenced = {reference.filename for reference in references}
    orphan_references = referenced - submitted_filenames
    if orphan_references:
        raise ValidationError(
            f"markdown references asset(s) not submitted: {sorted(orphan_references)}"
        )
    unreferenced_assets = submitted_filenames - referenced
    if unreferenced_assets:
        raise ValidationError(
            f"submitted asset(s) not referenced in markdown: {sorted(unreferenced_assets)}"
        )


def rewritten_markdown(markdown: str, job_id: str, filename_map: dict[str, str]) -> str:
    """Troca o nome de arquivo escolhido pelo cliente pelo nome opaco do servidor.

    O cliente nunca decide o nome final em disco -- fecha path traversal e
    colisao entre runbooks. O alt text (legenda) e preservado sem alteracao.
    """

    def replace(match: re.Match[str]) -> str:
        path_match = _ASSET_PATH.fullmatch(match.group("path"))
        if path_match is None:
            return match.group(0)
        new_filename = filename_map.get(path_match.group("filename"))
        if new_filename is None:
            return match.group(0)
        return f"![{match.group('alt')}](assets/{job_id}/{new_filename})"

    return _ASSET_REFERENCE.sub(replace, markdown)
