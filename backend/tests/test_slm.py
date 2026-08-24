import json

import httpx

from app.infrastructure.enrichment import DeterministicRunbookEnricher
from app.infrastructure.slm import OllamaCommandExtractor, OllamaRunbookEnricher


async def test_extrator_preserva_linhas_completas_e_rejeita_fragmentos() -> None:
    log = """operador@host:/$ uname -a
uname -a
Linux host 6.8.0 x86_64
operador@host:/$ df -h
df -h
Filesystem Size Used Avail Use% Mounted on
operador@host:/$ docker version
der version
Command 'der' not found, did you mean:
"""

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "commands": [
                                "der",
                                "version",
                                "der version",
                                "uname -a",
                                "df -h",
                                "docker version",
                            ]
                        }
                    )
                }
            },
        )

    extractor = OllamaCommandExtractor("http://slm.invalid", "modelo", 1)
    await extractor._client.aclose()
    extractor._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        commands = await extractor.extract(log)
    finally:
        await extractor.aclose()

    assert commands == ("uname -a", "df -h", "docker version")


async def test_num_thread_chega_ao_payload_somente_quando_configurado() -> None:
    enviados: list[dict[str, object]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(request.content)["options"])
        return httpx.Response(
            200, json={"message": {"content": '{"commands":["pwd"]}'}}
        )

    for num_thread, esperado in ((2, {"temperature": 0, "num_thread": 2}), (0, {"temperature": 0})):
        extractor = OllamaCommandExtractor(
            "http://slm.invalid", "modelo", 1, num_thread
        )
        await extractor._client.aclose()
        extractor._client = httpx.AsyncClient(
            base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
        )
        try:
            await extractor.extract("$ pwd\n/home/operador\n")
        finally:
            await extractor.aclose()
        assert enviados[-1] == esperado


async def test_extrator_aceita_comando_curto_observado() -> None:
    log = "$ pwd\n/home/operador\n"

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"commands":["pwd"]}'}},
        )

    extractor = OllamaCommandExtractor("http://slm.invalid", "modelo", 1)
    await extractor._client.aclose()
    extractor._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        commands = await extractor.extract(log)
    finally:
        await extractor.aclose()

    assert commands == ("pwd",)


async def test_extrator_descarta_stop_e_recupera_comandos_do_prompt() -> None:
    log = """operador@host:~$ uname -a
Linux host 6.8.0 x86_64
operador@host:~$ df -h
Filesystem Size Used Avail Use% Mounted on
operador@host:~$ lucien stop
"""

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"commands":["lucien stop"]}'}},
        )

    extractor = OllamaCommandExtractor("http://slm.invalid", "modelo", 1)
    await extractor._client.aclose()
    extractor._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        commands = await extractor.extract(log)
    finally:
        await extractor.aclose()

    assert commands == ("uname -a", "df -h")


async def test_inferencia_de_tags_respeita_idioma_configurado() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system_prompt = payload["messages"][0]["content"]
        assert "Generate all tags and suggestions in English." in system_prompt
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "tags": ["web_server"],
                            "objective": "Validate the web service.",
                            "architecture_prerequisites": ["Access to the host"],
                            "command_impacts": [
                                {
                                    "command": "systemctl status nginx",
                                    "impact": "Read-only service inspection.",
                                }
                            ],
                            "rollback_commands": [],
                        }
                    )
                }
            },
        )

    inferrer = OllamaRunbookEnricher("http://slm.invalid", "modelo", 1, "en")
    await inferrer._client.aclose()
    inferrer._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        enrichment = await inferrer.infer(("systemctl status nginx",))
    finally:
        await inferrer.aclose()

    assert enrichment.inferred_tags == ("web_server",)
    assert enrichment.suggestions.objective == "Validate the web service."
    assert enrichment.suggestions.command_impacts == (
        "Read-only service inspection.",
    )


async def test_enriquecedor_deterministico_linux() -> None:
    enricher = DeterministicRunbookEnricher("pt-br")
    result = await enricher.infer(
        ("sudo systemctl restart nginx", "docker ps", "rm -rf /var/log/velho")
    )

    assert "systemd" in result.inferred_tags
    assert "docker" in result.inferred_tags
    assert "requer_privilegio" in result.inferred_tags
    assert "criticidade_alta" in result.inferred_tags
    assert "indisponibilidade" in result.suggestions.command_impacts[0]
    assert result.suggestions.command_impacts[1] == ""
    assert "risco alto" in result.suggestions.command_impacts[2]
    assert result.suggestions.objective == "", "objetivo cede lugar à descrição do operador"
    assert any("systemd" in item for item in result.suggestions.architecture_prerequisites)


async def test_enriquecedor_deterministico_telecom() -> None:
    enricher = DeterministicRunbookEnricher("pt-br")
    result = await enricher.infer(
        (
            "show cable modem",
            "display ont info 0 1",
            "configure terminal",
            "shutdown",
            "clear ip bgp *",
            "ont delete 0 1 5",
        )
    )

    tags = result.inferred_tags
    assert "docsis" in tags and "huawei_olt" in tags and "bgp" in tags and "gpon" in tags
    impacts = result.suggestions.command_impacts
    assert impacts[0] == "", "comando de leitura não recebe impacto"
    assert "interface" in impacts[3]
    assert "sessões BGP" in impacts[4]
    assert "assinante" in impacts[5]
    # Inversão simétrica e segura; nada é inventado para comandos destrutivos.
    assert result.suggestions.rollback_commands == ("no shutdown",)


async def test_enriquecedor_deterministico_nunca_inventa_rollback() -> None:
    enricher = DeterministicRunbookEnricher("pt-br")
    result = await enricher.infer(("reload", "write erase", "ont delete 0 1 5"))

    assert result.suggestions.rollback_commands == ()
    assert all(impact for impact in result.suggestions.command_impacts)


async def test_enriquecedor_deterministico_em_ingles() -> None:
    enricher = DeterministicRunbookEnricher("en")
    result = await enricher.infer(("systemctl restart nginx",))

    assert "Restarts the unit" in result.suggestions.command_impacts[0]
    assert "command(s) captured" in result.suggestions.architecture_prerequisites[0]


async def test_extrator_rejeita_saida_de_comando_como_comando() -> None:
    # Reproduz o log real do jump server: a saída do `last` foi devolvida pela
    # SLM como se fosse comando, e a linha com prompt veio junto do comando limpo.
    log = """operador@lucien-jump:~$ last -a | head -20
operador pts/4        Fri Aug 14 20:32   still logged in    10.200.0.5
operador pts/5        Fri Aug 14 15:54 - 17:53  (01:58)     10.200.0.5
operador@lucien-jump:~$ ss -tlnp
State  Recv-Q Send-Q Local Address:Port
LISTEN 0      4096         0.0.0.0:22
"""

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "commands": [
                                "last -a | head -20",
                                "operador@lucien-jump:~$ ss -tlnp",
                                "operador pts/4        Fri Aug 14 20:32   still logged in    10.200.0.5",
                                "State  Recv-Q Send-Q Local Address:Port",
                                "LISTEN 0      4096         0.0.0.0:22",
                            ]
                        }
                    )
                }
            },
        )

    extractor = OllamaCommandExtractor("http://slm.invalid", "modelo", 1)
    await extractor._client.aclose()
    extractor._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        commands = await extractor.extract(log)
    finally:
        await extractor.aclose()

    # A linha com prompt é normalizada e deduplicada; as saídas somem.
    assert commands == ("last -a | head -20", "ss -tlnp")


async def test_observadas_permanecem_permissivas_sem_prompt_reconhecivel() -> None:
    from app.domain.transcript import observed_command_lines

    # Prompt zsh com `%` não casa o padrão; o fallback evita zerar a extração.
    log = "operador ~ % uname -a\nLinux host 6.8.0 x86_64\n"
    assert "Linux host 6.8.0 x86_64" in observed_command_lines(log)

    log_com_prompt = "user@host:~$ uname -a\nLinux host 6.8.0 x86_64\n"
    assert observed_command_lines(log_com_prompt) == {"uname -a"}


async def test_extrator_mantem_a_ordem_do_terminal_quando_a_slm_omite_o_acesso() -> None:
    """O comando de acesso e o primeiro passo, nao o ultimo.

    Sessao real de OLT ZTE: a SLM devolveu os comandos do equipamento e
    perdeu o `ssh`. Como o recall por prompt vinha depois da lista dela, o
    acesso era anexado no fim e o runbook mandava conectar depois de sair.
    """

    log = """operador@jump:~$ ssh U000004@10.200.0.4
ZTE#show optical-module-info xgei-1/10/1
Rx Power: -5.2 dBm
ZTE#show optical-module-info xgei-1/11/1
Rx Power: -6.1 dBm
ZTE#exit
Connection to 10.200.0.4 closed.
"""

    def responder(_: httpx.Request) -> httpx.Response:
        # A SLM omite o ssh e devolve o resto fora de ordem.
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "commands": [
                                "exit",
                                "show optical-module-info xgei-1/10/1",
                                "show optical-module-info xgei-1/11/1",
                            ]
                        }
                    )
                }
            },
        )

    extractor = OllamaCommandExtractor("http://slm.invalid", "modelo", 1)
    await extractor._client.aclose()
    extractor._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        commands = await extractor.extract(log)
    finally:
        await extractor.aclose()

    assert commands == (
        "ssh U000004@10.200.0.4",
        "show optical-module-info xgei-1/10/1",
        "show optical-module-info xgei-1/11/1",
        "exit",
    )


async def test_sem_prompt_reconhecido_a_ordem_da_slm_permanece() -> None:
    """A correcao de ordem nao pode custar o fallback sem prompt."""

    log = """uname -a
Linux host 6.8.0 x86_64
df -h
Filesystem Size Used Avail Use% Mounted on
"""

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"commands":["uname -a","df -h"]}'}},
        )

    extractor = OllamaCommandExtractor("http://slm.invalid", "modelo", 1)
    await extractor._client.aclose()
    extractor._client = httpx.AsyncClient(
        base_url="http://slm.invalid", transport=httpx.MockTransport(responder)
    )
    try:
        commands = await extractor.extract(log)
    finally:
        await extractor.aclose()

    assert commands == ("uname -a", "df -h")
