from app.domain.transcript import (
    completion_partials,
    extract_command_outputs,
    is_capture_control,
)


def test_relaciona_saida_ao_comando_sem_incluir_echo_ou_proximo_prompt() -> None:
    log = """operador@host:~$ uname -a
uname -a
Linux host 6.1 x86_64
operador@host:~$ df -h
df -h
Filesystem Size Used Avail Use% Mounted on
/dev/sda1 20G 10G 10G 50% /
operador@host:~$ lucien stop
"""

    outputs = extract_command_outputs(log, ("uname -a", "df -h"))

    assert outputs == (
        "Linux host 6.1 x86_64",
        "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 20G 10G 10G 50% /",
    )


def test_saida_longa_mantem_cinco_primeiras_e_ultima_linha() -> None:
    log = "operador@host:~$ comando\n" + "\n".join(
        f"linha {index}" for index in range(1, 9)
    )

    assert extract_command_outputs(log, ("comando",)) == (
        "linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5\n...\nlinha 8",
    )


def test_comando_nao_localizado_preserva_alinhamento_com_saida_vazia() -> None:
    outputs = extract_command_outputs(
        "host$ date\nFri Aug 14\n", ("inexistente", "date")
    )

    assert outputs == ("", "Fri Aug 14")


def test_linha_individual_tem_limite_de_tamanho() -> None:
    output = extract_command_outputs(
        "host$ printf\n" + ("x" * 2_100), ("printf",)
    )[0]

    assert len(output) == 2_000


def test_prompt_de_equipamento_de_rede_sem_espaco() -> None:
    """CLIs de OLT, CMTS e roteador colam o comando no prompt."""

    from app.domain.transcript import observed_command_lines, prompted_command_lines

    log = """U000004@lucien-jump:~$ ssh U000004@10.200.0.3
User Authentication
  Huawei Integrated Access Software (MA5800).
OLT-EXEMPLO-01>display board 0
  -------------------------------------------------------------------------
  SlotID  BoardName  Status          SubType0 SubType1    Online/Offline
  -------------------------------------------------------------------------
  8       H902MPLB   Active_normal
  9       H902MPLB   Standby_normal
  -------------------------------------------------------------------------
OLT-EXEMPLO-01>display ont version summary 0
                      ^
  % Unknown command, the error locates at '^'
OLT-EXEMPLO-01>quit
Connection to 10.200.0.3 closed.
"""

    assert prompted_command_lines(log) == (
        "ssh U000004@10.200.0.3",
        "display board 0",
        "display ont version summary 0",
        "quit",
    )

    # Com prompts reconhecidos, a whitelist deixa de aceitar linhas de saida.
    observadas = observed_command_lines(log)
    assert "display board 0" in observadas
    for saida in (
        "  8       H902MPLB   Active_normal",
        "Connection to 10.200.0.3 closed.",
        "display ontversionsummary0 versionsummary0 summary0 0",
    ):
        assert saida.strip() not in observadas, saida


def test_compatibilidade_do_prompt_posix_nao_regride() -> None:
    """A gramatica de shell POSIX precisa se comportar como antes da mudanca.

    O padrao original vira baseline: qualquer divergencia numa linha tipica de
    terminal local ou de servidor Linux e regressao, nao melhoria.
    """

    import re

    from app.domain.transcript import prompted_command

    baseline = re.compile(r"^(?:\([^\r\n)]+\)[ \t]+)?\S*[#$>][ \t]+(.+)$")

    def como_antes(linha: str) -> str | None:
        match = baseline.fullmatch(linha.strip())
        if match is None:
            return None
        return match.group(1).strip() or None

    linhas = [
        "operador@lucien-jump:~$ ls -la",
        "root@lucien-api:/opt# docker compose ps",
        "$ pwd",
        "# systemctl restart nginx",
        "(venv) user@host:~/proj$ python -m pytest",
        "total 48",
        "drwxr-xr-x  2 operador operador 4096 Aug 17 20:00 dist",
        "https://exemplo.interno/docs#ancora",
        "1>arquivo.txt",
        "2>&1 redirecionado",
        "#!/usr/bin/env bash",
        "-> resultado indentado",
        "->semespaco",
        "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.5 ms",
        "issue#123 corrigido no commit",
        "PR#45 merged por operador",
        "sha256:abc123def456  imagem",
        "Filesystem      Size  Used Avail Use% Mounted on",
    ]
    for linha in linhas:
        assert prompted_command(linha) == como_antes(linha), linha

    # Divergencias deliberadas: o padrao original errava nestas, entao manter o
    # comportamento antigo seria preservar o bug. Ficam separadas para que uma
    # regressao futura apareca como falha, e nao como "melhoria" silenciosa.
    melhorias = {
        "[root@rhel ~]# yum update": "yum update",
        "[root@rhel /var/log]# tail -n 5 messages": "tail -n 5 messages",
        "[operador@host ~]$ id": "id",
        "user@host:~/meu dir$ ls -la": "ls -la",
    }
    for linha, esperado in melhorias.items():
        assert como_antes(linha) is None, f"baseline ja cobria: {linha}"
        assert prompted_command(linha) == esperado, linha


def test_prompt_de_equipamento_cobre_os_fabricantes_usados() -> None:
    from app.domain.transcript import prompted_command

    esperado = {
        "OLT-EXEMPLO-01>display board 0": "display board 0",
        "OLT-EXEMPLO-01>quit": "quit",
        "Router#show running-config": "show running-config",
        "MA5800(config)#commit": "commit",
        "MA5800(config-if-gpon-0/1)#ont delete 1 5": "ont delete 1 5",
        "CMTS-01#show cable modem": "show cable modem",
        "ZXAN(config)#show gpon onu state": "show gpon onu state",
        "R1>enable": "enable",
    }
    for linha, comando in esperado.items():
        assert prompted_command(linha) == comando, linha


def test_prompt_com_espaco_nao_transforma_log_em_comando() -> None:
    """A ancora `usuario@host` e o que separa prompt de linha de saida.

    Sem ela, aceitar espaco antes do terminador faria qualquer log com
    colchetes virar comando -- e um comando inventado entra no runbook com
    aviso de execucao, que e pior do que um comando faltando.
    """

    from app.domain.transcript import prompted_command

    for linha in [
        "[2026-08-19 12:00:00] # reiniciando servico",
        "[INFO] > processando lote 3",
        "[WARN] $ variavel ausente",
        "[ERROR] Falha ao abrir /etc/passwd # permissao negada",
        "[1] 24601 # job em background",
        "Resultado [ok] $ 15,00 por unidade",
    ]:
        assert prompted_command(linha) is None, linha


def test_jump_encadeado_captura_os_comandos_do_equipamento() -> None:
    """A sessao real que motivou a correcao.

    O ssh local era reconhecido, entao a filtragem por prompt passava a
    valer -- e como o prompt do RHEL nao era reconhecido, todos os comandos
    do equipamento eram descartados. So o login sobrava no runbook.
    """

    from app.domain.transcript import observed_command_lines, prompted_command_lines

    log = """operador@estacao-exemplo:/$ ssh -o StrictHostKeyChecking=no U000004@root@10.0.0.1@jumper.exemplo
(U000004@root@10.0.0.1@jumper.exemplo) Password:
Last login: Tue Aug 19 23:34:43 2026
[root@equipamento ~]# ip route show
default via 10.0.0.254 dev eth0
[root@equipamento ~]# systemctl status frr
   Active: active (running)
[root@equipamento ~]# exit
logout
Connection to jumper.exemplo closed.
"""

    comandos = prompted_command_lines(log)

    assert "ip route show" in comandos
    assert "systemctl status frr" in comandos
    assert "exit" in comandos
    # O login continua sendo capturado: e o primeiro passo do procedimento.
    assert any(item.startswith("ssh -o StrictHostKeyChecking=no") for item in comandos)
    # E a saida do equipamento nao virou comando.
    observados = observed_command_lines(log)
    assert "default via 10.0.0.254 dev eth0" not in observados
    assert "Active: active (running)" not in observados
    assert "logout" not in observados


_CENARIOS = {
    "terminal local": (
        "operador@estacao:~$ uname -a\n"
        "Linux estacao 6.8.0 x86_64 GNU/Linux\n"
        "operador@estacao:~$ df -h\n"
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/sda1       100G   40G   60G  40% /\n"
        "operador@estacao:~$ exit\n"
    ),
    "ssh para servidor RedHat": (
        "operador@estacao:~$ ssh -o StrictHostKeyChecking=no root@10.0.0.1\n"
        "Last login: Wed Aug 20 10:00:00 2026\n"
        "[root@servidor-exemplo-01 ~]# systemctl status frr\n"
        "   Active: active (running)\n"
        "[root@servidor-exemplo-01 /var/log]# tail -20 messages | grep ACK\n"
        "2026.08.20 ACK for 10.200.0.2\n"
        "[root@servidor-exemplo-01 ~]# exit\n"
        "logout\n"
        "Connection to 10.0.0.1 closed.\n"
    ),
    "equipamento de rede": (
        "operador@jump:~$ ssh admin@10.0.0.9\n"
        "ZTE#show ip route\n"
        "default via 10.0.0.254\n"
        "ZTE(config)#interface xgei-1/10/1\n"
        "ZTE#exit\n"
    ),
    "servidor sem jump, saida enorme": (
        "operador@solaris:~$ snoop -c 400 -r port 53\n"
        + "".join(
            f"10.0.{i // 256}.{i % 256} -> 10.1.2.3 DNS R host{i}.exemplo.net\n"
            for i in range(400)
        )
        + "operador@solaris:~$ exit\n"
    ),
}


def test_visao_reduzida_preserva_todos_os_comandos() -> None:
    """A constraint que sustenta a truncagem.

    A redução vale só para o prompt da SLM. Se ela perdesse um comando, o
    modelo deixaria de ver um passo do procedimento -- e o formato de coleta
    de cada cenário mudaria.
    """

    from app.domain.transcript import prompt_view, prompted_command_lines

    for rotulo, log in _CENARIOS.items():
        completo = prompted_command_lines(log)
        reduzido = prompted_command_lines(prompt_view(log))
        assert completo == reduzido, rotulo
        assert completo, rotulo


def test_visao_reduzida_encolhe_saida_e_nao_comando() -> None:
    from app.domain.transcript import prompt_view

    log = _CENARIOS["servidor sem jump, saida enorme"]
    reduzido = prompt_view(log)

    # O bloco de 400 linhas vira 5 + marcador + ultima.
    assert len(reduzido) < len(log) // 20
    assert "..." in reduzido
    assert "snoop -c 400 -r port 53" in reduzido
    assert "exit" in reduzido
    # Primeira e ultima linha da saida sobrevivem, para o modelo saber o que
    # o comando produziu.
    assert "host0.exemplo.net" in reduzido
    assert "host399.exemplo.net" in reduzido


def test_extracao_de_saida_continua_recebendo_o_log_completo() -> None:
    """O runbook publicado nao pode mudar por causa da visao reduzida."""

    from app.domain.transcript import extract_command_outputs, prompted_command_lines

    log = _CENARIOS["ssh para servidor RedHat"]
    comandos = prompted_command_lines(log)

    saidas = extract_command_outputs(log, comandos)

    assert len(saidas) == len(comandos)
    juntas = "\n".join(saidas)
    assert "Active: active (running)" in juntas
    assert "ACK for 10.200.0.2" in juntas


def test_visao_reduzida_respeita_o_teto_de_caracteres() -> None:
    from app.domain.transcript import prompt_view

    # Muitos comandos, sem saida para colapsar: o teto final e o que segura.
    log = "".join(f"operador@host:~$ echo comando-numero-{i}\n" for i in range(5000))

    reduzido = prompt_view(log, max_characters=8_000)

    assert len(reduzido) <= 8_000
    assert reduzido.startswith("operador@host:~$ echo comando-numero-0")


def test_saida_nao_absorve_lucien_stop_sem_prompt() -> None:
    """A linha final da captura nao pode virar saida do ultimo comando.

    Reportado em producao: `lucien stop` chegou ao runbook publicado. O filtro
    de comandos de controle existia, mas so protegia a LISTA de comandos.
    Quando a linha final perde o prompt -- e o redesenho do readline faz isso
    ao converter `\r` em quebra --, ela deixa de ser comando para o extrator e
    era recolhida como saida do comando anterior.
    """

    log = (
        "U000001@host:~$ ssh U000001@10.0.0.1\n"
        "OLT01#quit\n"
        "\n"
        "  Configuration console exit, please retry to log on\n"
        "Connection to 10.0.0.1 closed.\n"
        "lucien stop\n"
    )

    (saida,) = extract_command_outputs(log, ("quit",))

    assert "Configuration console exit" in saida
    assert "lucien stop" not in saida


def test_controle_de_captura_reconhecido_com_e_sem_prompt() -> None:
    assert is_capture_control("lucien stop")
    assert is_capture_control("U000001@host:~$ lucien stop")
    assert is_capture_control("  sudo lucien upload  ")
    assert is_capture_control("/usr/local/bin/lucien start nome")
    # Qualquer subcomando, nao so os tres que controlam a gravacao. A lista
    # enumerada deixava passar o resto: um `lucien job sent` digitado antes de
    # `lucien start` chegou a selecao como se fosse passo do procedimento.
    assert is_capture_control("lucien job sent 00000000-0000-4000-8000-000000000000")
    assert is_capture_control("lucien reviews")
    assert is_capture_control("lucien")
    # A fronteira e o espaco. Outro executavel que apenas comece igual continua
    # sendo procedimento, e um comando de equipamento tambem.
    assert not is_capture_control("lucienctl start")
    assert not is_capture_control("display acl 3102")


def test_descarta_quadros_de_tab_do_cmts() -> None:
    """A sequencia real que produziu o runbook com cinco comandos parciais.

    O CMTS reexibe a linha inteira a cada Tab. Medido no equipamento: sem
    ESC[K e sem retorno de cursor, sao linhas fisicas com prompt proprio.
    """

    log = (
        "CMTS-01(config)#cable priva\n"
        "CMTS-01(config)#cable privacy host\n"
        "CMTS-01(config)#cable privacy hostl\n"
        "CMTS-01(config)#cable privacy hotli\n"
        "CMTS-01(config)#cable privacy hotlist cm b85e.71d0.a1c4\n"
        "CMTS-01(config)#end\n"
    )

    # Tres dos quatro quadros somem. `cable privacy hostl` sobrevive porque
    # `hotli` nao comeca com `hostl`: ali o operador corrigiu `hos` para
    # `hot`, e esse e o limite registrado no ultimo teste deste arquivo.
    assert completion_partials(log) == {
        "cable priva",
        "cable privacy host",
        "cable privacy hotli",
    }


def test_preserva_dois_comandos_reais_quando_um_e_prefixo_do_outro() -> None:
    """`show cable modem` e `show cable modem cable ... counters` sao dois.

    O primeiro foi executado e imprimiu a tabela. E a saida entre eles que
    separa o comando de verdade do quadro de Tab; sem essa condicao, a regra
    apagaria um passo do procedimento.
    """

    log = (
        "CMTS-01(config)#show cable modem\n"
        "MAC Address    US Packets\n"
        "b01f.f41a.ea76 27896662\n"
        "CMTS-01(config)#show cable modem cable 1/0/0 counters\n"
        "MAC Address    US Packets\n"
    )

    assert completion_partials(log) == set()


def test_tab_ambiguo_reexibe_a_linha_identica() -> None:
    """Tab sem completar nada: bell e a mesma linha de novo.

    Medido no CMTS: `18 car -> 18 car`, prefixo com o mesmo comprimento, que
    e igualdade. Entra na regra pelo mesmo caminho.
    """

    log = (
        "CMTS-01(config)#cable privacy hotlist\n"
        "CMTS-01(config)#cable privacy hotlist\n"
    )

    assert completion_partials(log) == {"cable privacy hotlist"}


def test_correcao_por_backspace_nao_e_coberta() -> None:
    """Limitacao registrada: `hostl` -> `hotli` nao e prefixo do seguinte.

    Nao ha sinal que separe isso de um comando que falhou e foi reescrito.
    Este teste existe para que a limitacao seja deliberada, e nao surpresa.
    """

    log = (
        "CMTS-01(config)#cable privacy hostl\n"
        "CMTS-01(config)#cable privacy hotli\n"
    )

    assert completion_partials(log) == set()


def test_recusa_por_segredo_nomeia_a_regra_e_nao_o_valor() -> None:
    """Reportado em producao: recusa sem motivo, impossivel de diagnosticar.

    A recusa acontece depois de o operador escrever o procedimento inteiro.
    Sem o nome da regra ele reabre o rascunho e procura as cegas.
    """

    from app.application import _mensagem_de_segredo
    from app.domain.ports import SecretScanResult

    mensagem = _mensagem_de_segredo(
        SecretScanResult(detected=True, rules=("lucien-snmp-community",))
    )

    assert "lucien-snmp-community" in mensagem
    assert "secret policy" in mensagem


def test_recusa_sem_regra_conhecida_mantem_a_mensagem_antiga() -> None:
    """Scanner anterior a esta mudanca nao informa regra.

    O veredito e o que importa; a regra e acrescimo. Um scanner antigo nao
    pode derrubar a publicacao nem produzir mensagem quebrada.
    """

    from app.application import _mensagem_de_segredo
    from app.domain.ports import SecretScanResult

    mensagem = _mensagem_de_segredo(SecretScanResult(detected=True))

    assert mensagem == "content blocked by the secret policy"


def test_adaptador_descarta_regra_que_nao_seja_identificador() -> None:
    """Ultima barreira antes do operador: nada que nao seja id atravessa.

    Se o scanner for adulterado ou mudar de formato, o Hub ainda recusa o que
    parecer conteudo -- o custo e perder o motivo, nunca vazar o valor.
    """

    from app.infrastructure.secret_scanner import _regras

    assert _regras(["lucien-snmp-community"]) == ("lucien-snmp-community",)
    assert _regras(["senha do cliente: abc123"]) == ()
    assert _regras(["x" * 200]) == ()
    assert _regras("nao e lista") == ()
    assert _regras(None) == ()
    assert _regras([1, True, None]) == ()
