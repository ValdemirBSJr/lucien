"""Enriquecimento determinístico, sem modelo de linguagem.

Alternativa ao `OllamaRunbookEnricher` para hosts onde a inferência não é viável.
Ao contrário da SLM, este provedor não pode alucinar: toda saída deriva de
tabelas revisáveis aplicadas aos comandos já extraídos e sanitizados. O conteúdo
continua sendo sugestão não autoritativa, sujeita à revisão humana obrigatória e
à mesma passagem por Secret Scanner e DLP no worker.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.domain.models import RunbookEnrichment, RunbookSuggestions
from app.domain.ports import RunbookEnricher
from app.domain.publication import Criticality, classify_criticality


@dataclass(frozen=True, slots=True)
class _CommandRule:
    """Regra de plataforma de rede casada contra a linha inteira do comando."""

    pattern: re.Pattern[str]
    tag: str
    prerequisite: str
    impact: str


# Ferramenta reconhecida -> (tag, termo de pré-requisito). A chave é o primeiro
# token do comando; `_TOOL_ALIASES` cobre variações que apontam para a mesma tag.
_TOOLS: dict[str, tuple[str, str]] = {
    "docker": ("docker", "Daemon Docker acessível ao operador"),
    "podman": ("podman", "Runtime Podman acessível ao operador"),
    "kubectl": ("kubernetes", "Contexto Kubernetes selecionado e autorizado"),
    "helm": ("helm", "Repositórios Helm configurados"),
    "systemctl": ("systemd", "Privilégio para gerenciar unidades systemd"),
    "journalctl": ("systemd", "Acesso ao journal do systemd"),
    "psql": ("postgresql", "Credencial de acesso ao PostgreSQL"),
    "pg_dump": ("postgresql", "Credencial de acesso ao PostgreSQL"),
    "mysql": ("mysql", "Credencial de acesso ao MySQL"),
    "redis-cli": ("redis", "Endpoint Redis alcançável"),
    "nginx": ("nginx", "Privilégio para recarregar o Nginx"),
    "apache2ctl": ("apache", "Privilégio para recarregar o Apache"),
    "git": ("git", "Credencial de acesso ao repositório"),
    "ansible": ("ansible", "Inventário e credenciais Ansible"),
    "ansible-playbook": ("ansible", "Inventário e credenciais Ansible"),
    "terraform": ("terraform", "Backend de estado Terraform acessível"),
    "iptables": ("firewall", "Privilégio de root para alterar o firewall"),
    "nft": ("firewall", "Privilégio de root para alterar o firewall"),
    "ufw": ("firewall", "Privilégio de root para alterar o firewall"),
    "ip": ("rede", "Privilégio para alterar interfaces de rede"),
    "ss": ("rede", "Acesso ao host para inspecionar sockets"),
    "netstat": ("rede", "Acesso ao host para inspecionar sockets"),
    "dig": ("dns", "Resolvedor DNS alcançável"),
    "nslookup": ("dns", "Resolvedor DNS alcançável"),
    "curl": ("http", "Conectividade de saída até o endpoint"),
    "wget": ("http", "Conectividade de saída até o endpoint"),
    "openssl": ("tls", "Material de chave e certificado disponível"),
    "ssh": ("ssh", "Chave SSH autorizada no destino"),
    "scp": ("ssh", "Chave SSH autorizada no destino"),
    "rsync": ("sincronizacao", "Acesso de leitura e escrita nos dois lados"),
    "mount": ("armazenamento", "Privilégio de root para montar volumes"),
    "umount": ("armazenamento", "Privilégio de root para desmontar volumes"),
    "lvextend": ("armazenamento", "Volume group com espaço livre"),
    "mkfs": ("armazenamento", "Dispositivo de bloco correto e sem uso"),
    "fdisk": ("armazenamento", "Privilégio de root sobre o dispositivo"),
    "df": ("armazenamento", "Acesso de leitura ao host"),
    "crontab": ("agendamento", "Privilégio sobre a crontab do usuário alvo"),
    "apt": ("pacotes", "Repositórios de pacote acessíveis"),
    "apt-get": ("pacotes", "Repositórios de pacote acessíveis"),
    "dnf": ("pacotes", "Repositórios de pacote acessíveis"),
    "yum": ("pacotes", "Repositórios de pacote acessíveis"),
    "sssd": ("ldap", "Domínio LDAP alcançável"),
    "ldapsearch": ("ldap", "Bind LDAP autorizado"),
}

_TOOL_ALIASES = {"docker-compose": "docker", "kubectl.exe": "kubectl"}

# (ferramenta, subcomando) -> impacto específico. Mais preciso que a
# classificação por criticidade e sempre derivado do comando observado.
_SUBCOMMAND_IMPACTS: dict[tuple[str, str], str] = {
    ("systemctl", "restart"): "Reinicia a unidade: há indisponibilidade momentânea do serviço.",
    ("systemctl", "stop"): "Para a unidade: o serviço fica indisponível até ser reiniciado.",
    ("systemctl", "start"): "Sobe a unidade: valide a configuração antes para evitar falha no boot.",
    ("systemctl", "disable"): "Remove a unidade do boot: o serviço não sobe após reiniciar o host.",
    ("systemctl", "enable"): "Habilita a unidade no boot: o serviço passa a subir automaticamente.",
    ("systemctl", "daemon-reload"): "Recarrega as definições de unidade; não reinicia serviços em execução.",
    ("docker", "rm"): "Remove o contêiner: dados fora de volume nomeado são perdidos.",
    ("docker", "rmi"): "Remove a imagem: exigirá novo pull ou build para recriar.",
    ("docker", "stop"): "Para o contêiner: o serviço publicado fica indisponível.",
    ("docker", "restart"): "Reinicia o contêiner: conexões em curso são encerradas.",
    ("docker", "prune"): "Remove recursos não utilizados: irreversível sem recriação.",
    ("docker", "build"): "Constrói imagem: consome CPU e disco no host de build.",
    ("kubectl", "delete"): "Remove objetos do cluster: pode causar indisponibilidade imediata.",
    ("kubectl", "apply"): "Altera o estado declarado: dispara rollout nos objetos afetados.",
    ("kubectl", "scale"): "Altera o número de réplicas: afeta capacidade e custo.",
    ("kubectl", "rollout"): "Dispara ou reverte rollout: pods são recriados.",
    ("terraform", "destroy"): "Destrói a infraestrutura gerenciada: irreversível sem novo apply.",
    ("terraform", "apply"): "Aplica mudanças de infraestrutura: revise o plano antes.",
    ("git", "push"): "Publica commits no remoto: afeta quem consome a branch.",
    ("git", "reset"): "Reescreve o ponteiro da branch: pode descartar trabalho local.",
    ("apt", "upgrade"): "Atualiza pacotes: serviços podem ser reiniciados pelo gerenciador.",
    ("apt-get", "upgrade"): "Atualiza pacotes: serviços podem ser reiniciados pelo gerenciador.",
    ("ufw", "enable"): "Ativa o firewall: sessões remotas podem cair se a regra de SSH faltar.",
}

def _rule(pattern: str, tag: str, prerequisite: str, impact: str) -> _CommandRule:
    return _CommandRule(
        re.compile(pattern, re.IGNORECASE), tag, prerequisite, impact
    )


# Plataformas de acesso e borda. A ordem importa: a primeira regra que casar
# define o impacto, então as destrutivas vêm antes das de leitura.
#
# Vendor só é afirmado quando a sintaxe é distintiva (`display`/`undo` = Huawei,
# `admin save` = Nokia SR OS, `show equipment ont` = Nokia ISAM). Comandos como
# `show cable modem` existem em Cisco, Arris e Casa: nesse caso a tag é do
# domínio (`docsis`), não do fabricante — afirmar vendor ali seria adivinhação.
_NETWORK_RULES: tuple[_CommandRule, ...] = (
    # --- Recarga e apagamento de configuração (qualquer plataforma) ---
    _rule(
        r"^\s*(?:admin\s+)?(?:reload|reboot)\b",
        "reinicio_equipamento",
        "Janela de manutenção aprovada e console fora de banda disponível",
        "Reinicia o equipamento inteiro: indisponibilidade total até o boot concluir.",
    ),
    _rule(
        r"^\s*reset\s+saved-configuration\b",
        "huawei_vrp",
        "Backup da configuração salva antes da execução",
        "Apaga a configuração de boot (Huawei): o equipamento sobe vazio no próximo reload.",
    ),
    _rule(
        r"\bwrite\s+erase\b|\berase\s+(?:startup-config|nvram:|flash:)",
        "apagamento_configuracao",
        "Backup da configuração de boot fora do equipamento",
        "Apaga a configuração de inicialização: irreversível sem backup externo.",
    ),
    _rule(
        r"^\s*format\s+(?:flash|cf|sd)",
        "apagamento_configuracao",
        "Backup completo da mídia do equipamento",
        "Formata a mídia de armazenamento: destrói imagem e configuração.",
    ),
    # --- OLT / GPON ---
    _rule(
        r"\b(?:ont|onu)\s+delete\b|^\s*(?:no|undo)\s+(?:ont|onu)\b|^\s*delete\s+(?:ont|onu)\b",
        "gpon",
        "Confirmação do assinante e do ID da ONT antes da remoção",
        "Remove a ONT/ONU provisionada: o assinante perde o serviço imediatamente.",
    ),
    _rule(
        r"^\s*(?:ont|onu)\s+(?:add|confirm)\b|\bont\s+add\b",
        "gpon",
        "Serial da ONT e perfil de serviço validados",
        "Provisiona ONT: revise perfil de linha e serviço antes de aplicar.",
    ),
    _rule(
        # ISAM usa `equipment ont`; `display ont` sem isso é sintaxe Huawei.
        r"^\s*(?:show|configure)\s+equipment\s+ont\b",
        "nokia_isam",
        "Sessão autenticada na OLT Nokia (ISAM)",
        "",
    ),
    _rule(
        r"^\s*display\s+ont\b|^\s*display\s+board\b",
        "huawei_olt",
        "Sessão autenticada na OLT Huawei (MA5800)",
        "",
    ),
    _rule(
        r"^\s*show\s+gpon\s+onu\b|\bgpon-onu_\d",
        "zte_olt",
        "Sessão autenticada na OLT ZTE (série C3xx)",
        "",
    ),
    _rule(
        r"^\s*(?:show|display)\s+(?:gpon|pon|ont|onu)\b",
        "gpon",
        "Sessão autenticada na OLT",
        "",
    ),
    # --- CMTS / DOCSIS ---
    _rule(
        r"\bclear\s+cable\s+modem\b[^\n]*\breset\b",
        "docsis",
        "Identificação do modem ou da faixa afetada",
        "Força reset dos modems selecionados: assinantes reconectam e perdem sessão.",
    ),
    _rule(
        r"^\s*(?:no\s+)?cable\s+(?:upstream|downstream)\b",
        "docsis",
        "Plano de RF revisado para o node afetado",
        "Altera portadora DOCSIS: impacta todos os assinantes do node.",
    ),
    _rule(
        r"^\s*show\s+cable\s+modem\b|^\s*show\s+cable\b",
        "docsis",
        "Sessão autenticada no CMTS",
        "",
    ),
    _rule(
        r"^\s*show\s+controllers\s+cable\b|\bcable-modem\b",
        "cisco_cbr",
        "Sessão autenticada no CMTS Cisco",
        "",
    ),
    # --- Roteamento de borda ---
    _rule(
        r"^\s*clear\s+ip\s+bgp\s+\*|^\s*reset\s+bgp\s+all\b",
        "bgp",
        "Janela aprovada: a reconvergência afeta todos os peers",
        "Derruba todas as sessões BGP: perda de rotas até a reconvergência completa.",
    ),
    _rule(
        r"^\s*(?:clear\s+ip\s+bgp|reset\s+bgp)\b",
        "bgp",
        "Peer BGP identificado e impacto de reconvergência avaliado",
        "Reinicia a sessão BGP indicada: as rotas aprendidas somem até reconvergir.",
    ),
    _rule(
        r"^\s*(?:show|display)\s+(?:ip\s+)?bgp\b|^\s*show\s+router\s+bgp\b",
        "bgp",
        "Sessão autenticada no roteador de borda",
        "",
    ),
    _rule(
        r"^\s*shutdown\b",
        "interface",
        "Confirmação da interface e do tráfego que ela transporta",
        "Desativa administrativamente a interface: o tráfego para imediatamente.",
    ),
    _rule(
        r"^\s*(?:no|undo)\s+shutdown\b",
        "interface",
        "Interface previamente validada",
        "Reativa a interface: o tráfego volta a fluir por ela.",
    ),
    # --- Modo de configuração e persistência ---
    _rule(
        r"^\s*(?:configure\s+terminal|config\s+t|configure\s+private|config)\b",
        "modo_configuracao",
        "Privilégio de configuração no equipamento",
        "Entra em modo de configuração: as alterações seguintes valem de imediato.",
    ),
    _rule(
        r"^\s*system-view\b|^\s*undo\s+",
        "huawei_vrp",
        "Privilégio de configuração no equipamento Huawei",
        "Sintaxe Huawei VRP: alteração de configuração corrente.",
    ),
    _rule(
        # `admin save` é distintivo de Nokia SR OS e é persistência: merece o
        # impacto específico, não o genérico por criticidade.
        r"^\s*admin\s+save\b",
        "nokia_sros",
        "Privilégio de configuração no equipamento Nokia (SR OS)",
        "Persiste a configuração corrente: passa a valer também após reload.",
    ),
    _rule(
        r"^\s*show\s+router\b|^\s*/configure\b",
        "nokia_sros",
        "Privilégio de configuração no equipamento Nokia (SR OS)",
        "",
    ),
    _rule(
        r"^\s*(?:commit|save|write\s+memory|wr\s+mem)\b|^\s*copy\s+running-config\s+startup-config\b",
        "persistencia_config",
        "Configuração corrente revisada antes de persistir",
        "Persiste a configuração corrente: passa a valer também após reload.",
    ),
    _rule(
        r"^\s*rollback\b",
        "persistencia_config",
        "Ponto de rollback conhecido e validado",
        "Retorna a configuração a um ponto anterior: mudanças recentes são descartadas.",
    ),
    _rule(
        r"^\s*display\b",
        "huawei_vrp",
        "Sessão autenticada no equipamento Huawei",
        "",
    ),
)

# Inversões seguras nas plataformas de rede: apenas pares simétricos de estado
# administrativo, preservando a sintaxe do fabricante detectado na própria linha.
_NETWORK_INVERSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*undo\s+shutdown\s*$", re.IGNORECASE), "shutdown"),
    (re.compile(r"^\s*no\s+shutdown\s*$", re.IGNORECASE), "shutdown"),
    (re.compile(r"^\s*shutdown\s*$", re.IGNORECASE), "no shutdown"),
)

_SUDO_PATTERN = re.compile(r"^\s*sudo\b")
_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Inversões seguras e literais. Só pares simétricos de ciclo de vida entram aqui;
# comandos destrutivos não têm rollback e nunca recebem sugestão inventada.
_INVERSIONS = {
    ("systemctl", "stop"): "start",
    ("systemctl", "start"): "stop",
    ("systemctl", "disable"): "enable",
    ("systemctl", "enable"): "disable",
    ("systemctl", "mask"): "unmask",
    ("docker", "stop"): "start",
    ("docker", "start"): "stop",
    ("docker", "pause"): "unpause",
}

_CRITICALITY_IMPACT = {
    Criticality.HIGH: (
        "Classificado como risco alto pelas regras do Hub: confirme alvo, "
        "janela e backup antes de executar."
    ),
    Criticality.MEDIUM: (
        "Classificado como risco médio pelas regras do Hub: altera estado ou "
        "exige privilégio elevado."
    ),
    Criticality.LOW: "",
}

_EN = {
    "Reinicia a unidade: há indisponibilidade momentânea do serviço.": "Restarts the unit: the service is briefly unavailable.",
    "Para a unidade: o serviço fica indisponível até ser reiniciado.": "Stops the unit: the service stays down until restarted.",
    "Sobe a unidade: valide a configuração antes para evitar falha no boot.": "Starts the unit: validate the configuration first to avoid boot failure.",
    "Remove a unidade do boot: o serviço não sobe após reiniciar o host.": "Removes the unit from boot: the service will not start after a reboot.",
    "Habilita a unidade no boot: o serviço passa a subir automaticamente.": "Enables the unit at boot: the service will start automatically.",
    "Recarrega as definições de unidade; não reinicia serviços em execução.": "Reloads unit definitions; does not restart running services.",
    "Remove o contêiner: dados fora de volume nomeado são perdidos.": "Removes the container: data outside a named volume is lost.",
    "Remove a imagem: exigirá novo pull ou build para recriar.": "Removes the image: a new pull or build is required to recreate it.",
    "Para o contêiner: o serviço publicado fica indisponível.": "Stops the container: the published service becomes unavailable.",
    "Reinicia o contêiner: conexões em curso são encerradas.": "Restarts the container: in-flight connections are dropped.",
    "Remove recursos não utilizados: irreversível sem recriação.": "Removes unused resources: irreversible without recreating them.",
    "Constrói imagem: consome CPU e disco no host de build.": "Builds an image: consumes CPU and disk on the build host.",
    "Remove objetos do cluster: pode causar indisponibilidade imediata.": "Deletes cluster objects: may cause immediate unavailability.",
    "Altera o estado declarado: dispara rollout nos objetos afetados.": "Changes declared state: triggers a rollout on affected objects.",
    "Altera o número de réplicas: afeta capacidade e custo.": "Changes replica count: affects capacity and cost.",
    "Dispara ou reverte rollout: pods são recriados.": "Triggers or reverts a rollout: pods are recreated.",
    "Destrói a infraestrutura gerenciada: irreversível sem novo apply.": "Destroys managed infrastructure: irreversible without a new apply.",
    "Aplica mudanças de infraestrutura: revise o plano antes.": "Applies infrastructure changes: review the plan first.",
    "Publica commits no remoto: afeta quem consome a branch.": "Publishes commits to the remote: affects branch consumers.",
    "Reescreve o ponteiro da branch: pode descartar trabalho local.": "Rewrites the branch pointer: may discard local work.",
    "Atualiza pacotes: serviços podem ser reiniciados pelo gerenciador.": "Upgrades packages: the manager may restart services.",
    "Ativa o firewall: sessões remotas podem cair se a regra de SSH faltar.": "Enables the firewall: remote sessions may drop without an SSH rule.",
    _CRITICALITY_IMPACT[Criticality.HIGH]: (
        "Classified as high risk by the Hub rules: confirm target, window and "
        "backup before executing."
    ),
    _CRITICALITY_IMPACT[Criticality.MEDIUM]: (
        "Classified as medium risk by the Hub rules: changes state or requires "
        "elevated privilege."
    ),
    "Daemon Docker acessível ao operador": "Docker daemon reachable by the operator",
    "Runtime Podman acessível ao operador": "Podman runtime reachable by the operator",
    "Contexto Kubernetes selecionado e autorizado": "Kubernetes context selected and authorized",
    "Repositórios Helm configurados": "Helm repositories configured",
    "Privilégio para gerenciar unidades systemd": "Privilege to manage systemd units",
    "Acesso ao journal do systemd": "Access to the systemd journal",
    "Credencial de acesso ao PostgreSQL": "PostgreSQL access credential",
    "Credencial de acesso ao MySQL": "MySQL access credential",
    "Endpoint Redis alcançável": "Reachable Redis endpoint",
    "Privilégio para recarregar o Nginx": "Privilege to reload Nginx",
    "Privilégio para recarregar o Apache": "Privilege to reload Apache",
    "Credencial de acesso ao repositório": "Repository access credential",
    "Inventário e credenciais Ansible": "Ansible inventory and credentials",
    "Backend de estado Terraform acessível": "Reachable Terraform state backend",
    "Privilégio de root para alterar o firewall": "Root privilege to change the firewall",
    "Privilégio para alterar interfaces de rede": "Privilege to change network interfaces",
    "Acesso ao host para inspecionar sockets": "Host access to inspect sockets",
    "Resolvedor DNS alcançável": "Reachable DNS resolver",
    "Conectividade de saída até o endpoint": "Outbound connectivity to the endpoint",
    "Material de chave e certificado disponível": "Key and certificate material available",
    "Chave SSH autorizada no destino": "SSH key authorized on the target",
    "Acesso de leitura e escrita nos dois lados": "Read and write access on both sides",
    "Privilégio de root para montar volumes": "Root privilege to mount volumes",
    "Privilégio de root para desmontar volumes": "Root privilege to unmount volumes",
    "Volume group com espaço livre": "Volume group with free space",
    "Dispositivo de bloco correto e sem uso": "Correct and unused block device",
    "Privilégio de root sobre o dispositivo": "Root privilege over the device",
    "Acesso de leitura ao host": "Read access to the host",
    "Privilégio sobre a crontab do usuário alvo": "Privilege over the target user crontab",
    "Repositórios de pacote acessíveis": "Reachable package repositories",
    "Domínio LDAP alcançável": "Reachable LDAP domain",
    "Bind LDAP autorizado": "Authorized LDAP bind",
}


class DeterministicRunbookEnricher(RunbookEnricher):
    """Deriva tags, pré-requisitos, impactos e rollback por tabela, sem SLM."""

    def __init__(self, runbook_language: Literal["pt-br", "en"] = "pt-br") -> None:
        self._language = runbook_language

    async def aclose(self) -> None:
        """Sem cliente HTTP para fechar; mantém a mesma interface do provedor SLM."""

    async def infer(
        self,
        commands: tuple[str, ...],
        sanitized_description: str | None = None,
    ) -> RunbookEnrichment:
        parsed = [_parse(command) for command in commands]
        matched = [_match_network_rule(command) for command in commands]
        criticality = classify_criticality(commands)

        tags = _dedupe(
            [
                rule.tag if rule else _TOOLS.get(tool, ("", ""))[0]
                for (tool, _), rule in zip(parsed, matched)
            ]
        )
        tags.append(f"criticidade_{criticality.value}")
        if any(_SUDO_PATTERN.match(command) for command in commands):
            tags.append("requer_privilegio")

        prerequisites = _dedupe(
            [
                self._translate(
                    rule.prerequisite if rule else _TOOLS.get(tool, ("", ""))[1]
                )
                for (tool, _), rule in zip(parsed, matched)
            ]
        )
        prerequisites.insert(0, self._summary(len(commands), criticality))

        impacts = tuple(
            self._impact(tool, subcommand, command, rule)
            for (tool, subcommand), command, rule in zip(parsed, commands, matched)
        )
        rollback = _dedupe(
            [
                inverted
                for (tool, subcommand), command in zip(parsed, commands)
                if (inverted := _invert(tool, subcommand, command))
            ]
        )

        return RunbookEnrichment(
            inferred_tags=tuple(tags[:12]),
            suggestions=RunbookSuggestions(
                # Objetivo fica vazio de propósito: sem modelo não há como
                # sintetizá-lo honestamente, e assim a descrição do operador
                # (`lucien start -d`) prevalece no template do CLI.
                objective="",
                architecture_prerequisites=tuple(prerequisites[:8]),
                command_impacts=impacts,
                rollback_commands=tuple(rollback[:10]),
            ),
        )

    def _summary(self, command_count: int, criticality: Criticality) -> str:
        if self._language == "en":
            return (
                f"{command_count} command(s) captured; criticality "
                f"{criticality.value} by deterministic Hub rules, without a language model."
            )
        return (
            f"{command_count} comando(s) capturado(s); criticidade "
            f"{criticality.value} pelas regras determinísticas do Hub, sem modelo de linguagem."
        )

    def _impact(
        self, tool: str, subcommand: str, command: str, rule: _CommandRule | None
    ) -> str:
        if rule is not None and rule.impact:
            return self._translate(rule.impact)
        specific = _SUBCOMMAND_IMPACTS.get((tool, subcommand))
        if specific:
            return self._translate(specific)
        return self._translate(_CRITICALITY_IMPACT[classify_criticality([command])])

    def _translate(self, text: str) -> str:
        return _EN.get(text, text) if self._language == "en" else text


def _parse(command: str) -> tuple[str, str]:
    """Devolve (ferramenta, subcomando) ignorando `sudo` e variáveis de ambiente."""

    tokens = command.split()
    index = 0
    while index < len(tokens) and (
        tokens[index] == "sudo" or _ASSIGNMENT_PATTERN.match(tokens[index])
    ):
        index += 1
    if index >= len(tokens):
        return "", ""
    tool = tokens[index].rsplit("/", 1)[-1].lower()
    tool = _TOOL_ALIASES.get(tool, tool)
    subcommand = ""
    for candidate in tokens[index + 1 :]:
        if not candidate.startswith("-"):
            subcommand = candidate.lower()
            break
    return tool, subcommand


def _match_network_rule(command: str) -> _CommandRule | None:
    for rule in _NETWORK_RULES:
        if rule.pattern.search(command):
            return rule
    return None


def _invert(tool: str, subcommand: str, command: str) -> str:
    """Inverte apenas pares simétricos de ciclo de vida, preservando o alvo."""

    for pattern, replacement in _NETWORK_INVERSIONS:
        if pattern.match(command):
            return replacement

    inverted = _INVERSIONS.get((tool, subcommand))
    if not inverted:
        return ""
    tokens = command.split()
    for position, token in enumerate(tokens):
        if token.lower() == subcommand:
            return " ".join((*tokens[:position], inverted, *tokens[position + 1 :]))
    return ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique
