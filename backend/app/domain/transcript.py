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

# Os comandos do próprio CLI. Nunca são procedimento: quem os lê num runbook
# não aprende nada e ainda é induzido a executá-los.
#
# Qualquer invocação de `lucien`, não só `start`, `stop` e `upload`. Enumerar
# subcomandos deixava passar tudo que não estava na lista -- um `lucien job sent`
# da sessão anterior chegou à seleção como se fosse passo do procedimento. O
# CLI é quem grava; o que ele faz está fora do que se está gravando, e um
# subcomando novo passa a ser coberto sem ninguém lembrar de vir aqui.
#
# `lucien` seguido de espaço ou fim de linha: `lucienctl` nao casa.
#
# Vive no domínio porque a regra é a mesma nos dois lugares que precisam dela --
# a extração de comandos e o recorte da saída. Enquanto morava só na
# infraestrutura, a saída ficava desprotegida: bastava a linha final perder o
# prompt para `lucien stop` entrar no bloco do comando anterior e chegar ao
# documento publicado.
CAPTURE_CONTROL_COMMAND = re.compile(
    r"^(?:sudo[ \t]+)?(?:\S*/)?lucien(?:[ \t]|$)"
)


def is_capture_control(line: str) -> bool:
    """Informa se a linha é um comando de controle da captura.

    Aceita a linha com prompt (`user@host:~$ lucien stop`) e sem prompt, que é
    a forma que escapava: sem prompt a linha não é comando para o extrator, e
    por isso era tratada como saída.
    """

    candidato = prompted_command(line) or line.strip()
    return bool(candidato) and CAPTURE_CONTROL_COMMAND.match(candidato) is not None


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


def completion_partials(log: str) -> set[str]:
    """Estados intermediários deixados por completação com Tab.

    CLIs de equipamento reexibem a linha inteira a cada Tab: emitem `\\r\\n`, o
    prompt e o comando como está até ali. Cada quadro vira uma linha física, e
    cada linha vira um comando -- foi assim que um runbook do CMTS saiu com
    `cable priva`, `cable privacy host`, `cable privacy hostl` e mais dois,
    quando só o último foi executado.

    Medido no equipamento: a reexibição não usa `ESC[K` nem retorno de cursor.
    São linhas de verdade, então a evidência está ENTRE elas.

    Duas condições, e as duas importam:

    - o comando é prefixo do seguinte (igualdade inclusa, que é o Tab ambíguo
      reexibindo a linha sem completar nada);
    - nada foi impresso entre os dois.

    A segunda é o que separa o quadro de Tab de dois comandos legítimos. Em
    `show cable modem` seguido de `show cable modem cable 1/0/0 counters`, o
    primeiro foi executado e imprimiu a tabela; o quadro de Tab não imprime
    nada, porque nunca foi executado.

    Limitação conhecida: correção por backspace que troca caracteres no meio
    -- `hostl` virando `hotli` -- não é prefixo do seguinte e continua
    aparecendo. Não há sinal que a distinga de um comando que falhou e foi
    reescrito, e inventar um apagaria comando de verdade.
    """

    anterior: str | None = None
    houve_saida = False
    parciais: set[str] = set()

    for raw_line in log.splitlines():
        comando = prompted_command(raw_line)
        if comando is None:
            if raw_line.strip() and not _PROMPT_ONLY_PATTERN.fullmatch(
                raw_line.strip()
            ):
                houve_saida = True
            continue
        if anterior is not None and not houve_saida and comando.startswith(anterior):
            parciais.add(anterior)
        anterior = comando
        houve_saida = False
    return parciais


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
            # `lucien stop` sem prompt não é comando para o extrator, então
            # caía aqui como saída do comando anterior. Encerra o bloco em vez
            # de pular a linha: o que vem depois do fim da captura não é saída
            # de comando nenhum.
            if is_capture_control(line):
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
