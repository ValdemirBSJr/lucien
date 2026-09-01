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
    path: str
    raw: str


def extract_asset_references(
    markdown: str,
    expected_job_id: str,
    already_existing_paths: frozenset[str] = frozenset(),
) -> tuple[AssetReference, ...]:
    """Acha toda imagem Markdown e garante que pertence a este job.

    A legenda (alt text) e obrigatoria: e a ultima barreira humana contra
    vazamento, alem do OCR -- nunca em lugar dele.

    `already_existing_paths` sao referencias completas (`assets/<job>/<arquivo>`)
    que ja apareciam no Markdown publicado anteriormente desta mesma linhagem
    -- uma revisao pode heranca-las de um ancestral distante, cujo job_id nunca
    bateria com `expected_job_id`. Sem essa excecao, so seria possivel revisar
    mantendo uma imagem se o job_id dela coincidisse com o da fonte imediata.
    """

    references: list[AssetReference] = []
    for match in _ASSET_REFERENCE.finditer(markdown):
        alt = match.group("alt").strip()
        if not alt:
            raise ValidationError(
                "every image needs a non-empty caption (alt text); "
                "the operator is the last line of defense against leaks"
            )
        raw_path = match.group("path")
        path_match = _ASSET_PATH.fullmatch(raw_path)
        if path_match is None:
            raise ValidationError(
                f"image reference '{raw_path}' must point to "
                "assets/<job_id>/<filename>"
            )
        if path_match.group("job_id") != expected_job_id and raw_path not in already_existing_paths:
            raise ValidationError("image reference points to a different job's assets")
        references.append(
            AssetReference(
                alt=alt,
                filename=path_match.group("filename"),
                path=raw_path,
                raw=match.group(0),
            )
        )
    return tuple(references)


def previously_published_asset_paths(markdown: str) -> frozenset[str]:
    """Referencias completas de imagem (`assets/<job_id>/<arquivo>`) que ja
    apareciam num Markdown anteriormente publicado.

    So serve para eximir uma revisao de reenviar uma imagem que herdou sem
    alteracao -- esse texto ja passou pelos gates de segredo/OCR quando foi
    publicado da primeira vez, entao confiar nas referencias dele e seguro.
    """

    paths: set[str] = set()
    for match in _ASSET_REFERENCE.finditer(markdown):
        raw_path = match.group("path")
        if _ASSET_PATH.fullmatch(raw_path) is not None:
            paths.add(raw_path)
    return frozenset(paths)


def validate_asset_completeness(
    references: tuple[AssetReference, ...],
    submitted_filenames: frozenset[str],
    already_existing_paths: frozenset[str] = frozenset(),
) -> None:
    """Checagem 1:1: toda referencia NOVA tem asset enviado e vice-versa.

    Uma referencia cujo caminho completo esta em `already_existing_paths` foi
    herdada sem alteracao de uma versao anterior -- exigir reenvio dela so
    para manter uma imagem que nao mudou obrigaria o operador a colar de novo
    algo que o Hub ja tem. Sem essa excecao, uma referencia orfa publicaria um
    link quebrado, e um asset nao referenciado ficaria escondido a toa.
    """

    new_references = [
        reference for reference in references if reference.path not in already_existing_paths
    ]
    referenced = {reference.filename for reference in new_references}
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
