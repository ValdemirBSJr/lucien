import re


# Duas gramáticas de prompt convivem no mesmo log.
#
# A primeira alternativa é a original e permanece byte a byte igual: shells
# POSIX separam o comando do prompt por espaço (`user@host:~$ ls`). Toda sessão
# local ou em servidor Linux continua sendo reconhecida exatamente como antes.
#
# A segunda cobre CLIs de equipamento de rede, que colam o comando no prompt
# (`OLT01>display board 0`, `Router#show run`, `MA5800(config)#commit`). Ela é
# deliberadamente restrita à forma de um hostname — dois ou mais caracteres
# alfanuméricos, ponto, hífen ou sublinhado, com modo opcional entre parênteses
# — para não capturar `https://host/doc#ancora` nem `1>arquivo` que apareçam na
# saída de comandos Linux.
# Um prompt pode conter espaco antes do caractere final -- `[root@host ~]#` e o
# padrao de root no RHEL/CentOS. `\S*` nunca atravessa esse espaco, entao esses
# prompts nao eram reconhecidos e, pior, bastava um prompt reconhecido em outro
# ponto do log para que TODOS os comandos do equipamento fossem descartados.
#
# As duas alternativas com espaco exigem `usuario@host` no inicio da linha, o que
# uma linha de saida quase nunca tem. Sem essa ancora, um log como
# `[2026-08-19 12:00:00] # mensagem` viraria comando. O quantificador nao-guloso
# para no primeiro terminador: em `~$ cd /tmp && echo $ ok`, o guloso pegaria o
# `$` final e devolveria `ok` como comando.
_PROMPTED_COMMAND_PATTERN = re.compile(
    r"^(?:\([^\r\n)]+\)[ \t]+)?"
    r"(?:"
    r"\S*[#$>][ \t]+"
    r"|\[[A-Za-z0-9._-]+@[A-Za-z0-9._-]+[^\]\r\n]{0,120}?\][#$][ \t]+"
    r"|[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\r\n]{0,120}?[#$][ \t]+"
    r"|[A-Za-z0-9][A-Za-z0-9._-]+(?:\([^)\s]*\))?[#>][ \t]*(?=[A-Za-z])"
    r")"
    r"(.+)$"
)
_PROMPT_ONLY_PATTERN = re.compile(r"^(?:\([^\r\n)]+\)[ \t]+)?\S*[#$>][ \t]*$")


def prompted_command(line: str) -> str | None:
    """Retorna o comando quando a linha contém um prompt reconhecível."""

    match = _PROMPTED_COMMAND_PATTERN.fullmatch(line.strip())
    if match is None:
        return None
    command = match.group(1).strip()
    return command or None


def prompted_command_lines(log: str, limit: int = 200) -> tuple[str, ...]:
    """Extrai comandos de prompts sem interpretar a saída do terminal."""

    commands: list[str] = []
    for raw_line in log.splitlines():
        command = prompted_command(raw_line)
        if command is not None:
            commands.append(command)
            if len(commands) == limit:
                break
    return tuple(commands)


def observed_command_lines(log: str) -> set[str]:
    """Linhas do log aceitáveis como comando digitado pelo operador.

    Quando o log tem ao menos um prompt reconhecível, somente os comandos
    extraídos desses prompts entram no conjunto. Aceitar qualquer linha faria a
    saída dos comandos passar pela validação e aparecer na seleção do runbook.

    Sem nenhum prompt reconhecível — prompt exótico ou captura parcial — o
    conjunto volta a ser permissivo. É menos preciso, mas evita que a extração
    zere e o Job falhe com NO_COMMANDS.
    """

    prompted = prompted_command_lines(log)
    if prompted:
        return set(prompted)
    return {line.strip() for line in log.splitlines() if line.strip()}


def prompt_view(
    log: str,
    *,
    leading_lines: int = 5,
    max_line_characters: int = 2_000,
    max_characters: int = 8_000,
) -> str:
    """Reduz o log para caber no prompt da SLM sem perder nenhum comando.

    Toda linha reconhecida como comando digitado é preservada na íntegra. Só
    os blocos de saída entre elas são colapsados, com a mesma política que
    `extract_command_outputs` já aplica ao runbook publicado: as primeiras
    linhas, um marcador e a última.

    Esta visão vale exclusivamente para o payload do modelo. O filtro de
    comandos observados, o passe de recall por prompt e a extração de saída
    continuam recebendo o log completo -- por isso a redução não pode encolher
    o conjunto de comandos extraídos, apenas o que a SLM lê para ordená-los.

    O corte importa porque uma sessão de captura de pacotes chega com
    centenas de linhas de saída e uma dúzia de comandos; mandar tudo é caro e
    afoga a instrução do sistema no volume.
    """

    linhas = [linha.rstrip() for linha in log.replace("\r\n", "\n").split("\n")]
    reduzido: list[str] = []
    bloco: list[str] = []

    def descarrega() -> None:
        if not bloco:
            return
        limitado = [linha[:max_line_characters] for linha in bloco]
        if len(limitado) > leading_lines:
            limitado = [*limitado[:leading_lines], "...", limitado[-1]]
        reduzido.extend(limitado)
        bloco.clear()

    for linha in linhas:
        if prompted_command(linha) is not None:
            descarrega()
            reduzido.append(linha[:max_line_characters])
            continue
        bloco.append(linha)
    descarrega()

    texto = "\n".join(reduzido)
    if len(texto) <= max_characters:
        return texto
    # Ultimo recurso: uma sessao com comandos demais ainda pode estourar. O
    # corte vem do fim porque o inicio costuma trazer o acesso, que e o
    # primeiro passo do procedimento.
    return texto[:max_characters]


def extract_command_outputs(
    log: str,
    commands: tuple[str, ...],
    *,
    leading_lines: int = 5,
    max_line_characters: int = 2_000,
) -> tuple[str, ...]:
    """Relaciona cada comando à saída observada até o próximo prompt/comando.

    O recorte mantém as cinco primeiras linhas e, quando houver mais conteúdo,
    acrescenta um marcador e a última linha. O limite por linha impede que uma
    única saída sem quebras aumente o payload de forma descontrolada.
    """

    lines = [line.rstrip() for line in log.splitlines()]
    outputs: list[str] = []
    cursor = 0

    for index, command in enumerate(commands):
        position = _find_command(lines, command, cursor)
        if position is None:
            outputs.append("")
            continue

        cursor = position + 1
        # PTYs podem ecoar o comando logo após a linha que contém o prompt.
        while cursor < len(lines) and _line_is_command(lines[cursor], command):
            cursor += 1

        remaining = set(commands[index + 1 :])
        captured: list[str] = []
        while cursor < len(lines):
            line = lines[cursor]
            prompted = prompted_command(line)
            if prompted is not None or line.strip() in remaining:
                break
            if not _PROMPT_ONLY_PATTERN.fullmatch(line.strip()):
                captured.append(line)
            cursor += 1

        normalized = _trim_empty_edges(captured)
        bounded = [line[:max_line_characters] for line in normalized]
        if len(bounded) > leading_lines:
            bounded = [*bounded[:leading_lines], "...", bounded[-1]]
        outputs.append("\n".join(bounded))

    return tuple(outputs)


def _find_command(lines: list[str], command: str, start: int) -> int | None:
    for index in range(start, len(lines)):
        if _line_is_command(lines[index], command):
            return index
    return None


def _line_is_command(line: str, command: str) -> bool:
    stripped = line.strip()
    return stripped == command or prompted_command(line) == command


def _trim_empty_edges(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]
