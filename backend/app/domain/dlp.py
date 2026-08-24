import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """Resultado da redação sem manter o valor sensível removido."""

    text: str
    replacements: int


_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----.*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)\b(?P<prefix>authorization\s*:\s*(?:bearer|basic)\s+)"
    r"(?P<value>[^\s`\"']+)"
)
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?P<scheme>[a-z][a-z0-9+.-]*://)"
    r"(?P<user>[^/\s:@]+):(?P<password>[^/\s@]+)@"
)
# Restrito ao comando AUTH em posição de comando: início de linha, prompt do
# redis-cli terminado em " >" ou invocação explícita de redis-cli. Não aceitamos
# marcadores genéricos #/$ para não confundir headings Markdown com prompts.
_REDIS_AUTH_PATTERN = re.compile(
    r"(?m)(?P<prefix>(?:"
    r"^[ \t]*AUTH[ \t]+|"
    r"^[ \t]*redis-cli\b[^\r\n]*?[ \t]+AUTH[ \t]+|"
    r"^[ \t]*\S+>[ \t]*AUTH[ \t]+"
    r"))(?P<value>[^\r\n]+)"
)
_CLI_SECRET_PATTERN = re.compile(
    r"(?im)(?P<prefix>--(?P<key>password|passwd|pwd|token|secret|api-key|apikey|key)\s+)"
    r"(?:\"(?P<double>[^\"\r\n]*)\"|'(?P<single>[^'\r\n]*)'|"
    r"(?P<plain>[^\s`\"']+))"
)
_STRUCTURED_SECRET_PATTERN = re.compile(
    r"(?im)(?<![A-Z0-9_])(?P<key_quote>[\"']?)"
    r"(?P<key>[A-Z0-9_.-]*(?:PASSWORD|PASSWD|PWD|TOKEN|SECRET|"
    r"API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|"
    r"REDIS[_-]?(?:USER|USERNAME))[A-Z0-9_.-]*)(?P=key_quote)"
    r"(?P<separator>[ \t]*[:=][ \t]*)"
    r"(?:\"(?P<double>[^\"\r\n]*)\"|'(?P<single>[^'\r\n]*)'|"
    r"(?P<plain>[^\s,}\]]+))"
)
# O handshake de MFA entra no log como saida do comando de acesso: ele nao e
# um passo do procedimento e carrega dado pessoal. O telefone chega ja mascarado
# pelo provedor, mas os ultimos digitos identificam a pessoa, e o desafio tambem
# revela o fator em uso -- informacao util so para quem prepara um ataque de
# fadiga de push.
#
# Redigir o desafio inteiro, e nao so o telefone, evita perseguir cada formato
# que um provedor de MFA venha a imprimir. Os exemplos abaixo sao sinteticos.
_MFA_CHALLENGE_PATTERNS = (
    # `1. Duo Push to +XX XX XXXXX-5081`, `2. SMS passcodes to +55 ...`
    (
        re.compile(
            r"(?im)^(?P<recuo>[ \t]*)(?P<indice>[0-9]{1,2}\.[ \t]*)?"
            r"(?P<canal>Duo Push|SMS passcodes?|Phone call)"
            r"[ \t]+to[ \t]+[^\r\n]+$"
        ),
        lambda match: (
            f"{match.group('recuo')}{match.group('indice') or ''}"
            f"{match.group('canal')} to SEU_FATOR_MFA_AQUI"
        ),
    ),
    # `RADIUS challenge: Duo two-factor login for U000004`
    (
        re.compile(
            r"(?im)(?P<prefixo>\b(?:RADIUS challenge|"
            r"Duo two-factor login for))[ \t:]*[^\r\n]*"
        ),
        lambda match: f"{match.group('prefixo')}: SEU_DESAFIO_MFA_AQUI",
    ),
    # Telefone parcialmente mascarado que sobreviva fora das linhas acima.
    (
        re.compile(r"\+(?:[0-9X]{1,4}[ \t-]?){2,5}[0-9X]{2,5}-[0-9]{2,4}\b"),
        lambda _match: "SEU_TELEFONE_AQUI",
    ),
)

_LITERAL_SECRET_PATTERNS = (
    (
        re.compile(r"\bluc_(?:tmp_)?[A-Za-z0-9_-]{32,}\b"),
        "SEU_TOKEN_LUCIEN_AQUI",
    ),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "SUA_KEY_GITHUB_AQUI"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "SUA_API_KEY_AQUI"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "SUA_ACCESS_KEY_AWS_AQUI"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "SEU_TOKEN_JWT_AQUI",
    ),
)


def _placeholder_for(key: str) -> str:
    normalized = key.upper().replace("-", "_").replace(".", "_")
    if "REDIS" in normalized and ("USER" in normalized or "USERNAME" in normalized):
        return "SEU_USER_REDIS_AQUI"
    if "REDIS" in normalized and any(
        marker in normalized for marker in ("PASSWORD", "PASSWD", "PWD")
    ):
        return "SUA_SENHA_REDIS_AQUI"
    if "EVOLUTION" in normalized and "KEY" in normalized:
        return "SUA_KEY_EVOLUTION_AQUI"
    if any(marker in normalized for marker in ("PASSWORD", "PASSWD", "PWD")):
        return "SUA_SENHA_AQUI"
    if "TOKEN" in normalized:
        return "SEU_TOKEN_AQUI"
    if "SECRET" in normalized:
        return "SEU_SEGREDO_AQUI"
    return "SUA_KEY_AQUI"


def _replace_and_count(
    value: str,
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str],
) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        new_value = replacement(match) if callable(replacement) else replacement
        if new_value != match.group(0):
            count += 1
        return new_value

    return pattern.sub(replace, value), count


def _render_placeholder_value(match: re.Match[str], key: str) -> str:
    placeholder = _placeholder_for(key)
    if match.group("double") is not None:
        return f'"{placeholder}"'
    if match.group("single") is not None:
        return f"'{placeholder}'"
    return placeholder


def sanitize_secrets(value: str) -> SanitizationResult:
    """Redige padrões conhecidos antes de SLM, persistência ou publicação."""

    sanitized = value
    replacements = 0

    def apply(
        pattern: re.Pattern[str],
        replacement: str | Callable[[re.Match[str]], str],
    ) -> None:
        nonlocal sanitized, replacements
        sanitized, count = _replace_and_count(sanitized, pattern, replacement)
        replacements += count

    apply(_PRIVATE_KEY_PATTERN, "[SUA_CHAVE_PRIVADA_AQUI]")
    for padrao_mfa, redacao_mfa in _MFA_CHALLENGE_PATTERNS:
        apply(padrao_mfa, redacao_mfa)
    apply(
        _URL_CREDENTIAL_PATTERN,
        lambda match: f"{match.group('scheme')}SEU_USUARIO_AQUI:SUA_SENHA_AQUI@",
    )
    apply(
        _AUTHORIZATION_PATTERN,
        lambda match: f"{match.group('prefix')}SUA_CREDENCIAL_AQUI",
    )
    apply(
        _REDIS_AUTH_PATTERN,
        lambda match: f"{match.group('prefix')}SUA_SENHA_REDIS_AQUI",
    )
    apply(
        _CLI_SECRET_PATTERN,
        lambda match: f"{match.group('prefix')}"
        f"{_render_placeholder_value(match, match.group('key'))}",
    )

    def replace_structured(match: re.Match[str]) -> str:
        replacement_value = _render_placeholder_value(match, match.group("key"))
        key_quote = match.group("key_quote")
        return (
            f"{key_quote}{match.group('key')}{key_quote}"
            f"{match.group('separator')}{replacement_value}"
        )

    apply(_STRUCTURED_SECRET_PATTERN, replace_structured)
    for pattern, placeholder in _LITERAL_SECRET_PATTERNS:
        apply(pattern, placeholder)

    return SanitizationResult(text=sanitized, replacements=replacements)
