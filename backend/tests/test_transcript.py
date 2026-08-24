from app.domain.transcript import extract_command_outputs


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
