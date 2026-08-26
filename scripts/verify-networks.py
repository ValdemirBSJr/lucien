#!/usr/bin/env python3
"""Confere a segmentação de rede do Compose renderizado (JSON pelo stdin).

Segmentação é o tipo de proteção que se desfaz sem barulho: acrescentar uma
rede a um serviço para resolver um problema pontual é uma linha, e nada
reclama depois. Este portão declara quem pode alcançar a internet e falha
quando a lista muda -- inclusive quando um serviço novo aparece sem decisão
tomada a respeito.
"""

import json
import sys

# Redes com rota para fora, e a razão de cada uma existir.
EGRESSO = {
    "git_egress": "publish to GitHub or Gitea",
    "slm_egress": "pull the model with `ollama pull`",
    "wiki_egress": "clone the wiki repository",
}

# Quem tem permissão de sair, e por qual rede. Serviço fora deste mapa não
# pode tocar em nenhuma rede de egresso.
AUTORIZADOS = {
    "hub": {"git_egress"},
    "slm": {"slm_egress"},
    "wiki-builder": {"wiki_egress"},
}


def main() -> int:
    dados = json.load(sys.stdin)
    redes = dados.get("networks", {})
    servicos = dados.get("services", {})
    falhas: list[str] = []

    for nome, definicao in redes.items():
        interna = bool((definicao or {}).get("internal"))
        if nome in EGRESSO and interna:
            falhas.append(f"network {nome} is internal but needs to {EGRESSO[nome]}")
        if nome not in EGRESSO and not interna:
            falhas.append(
                f"network {nome} is neither internal nor declared as egress; "
                "add the reason to EGRESSO or mark it internal: true"
            )

    for nome, definicao in servicos.items():
        anexadas = set((definicao or {}).get("networks") or {})
        saidas = anexadas & set(EGRESSO)
        permitidas = AUTORIZADOS.get(nome, set())
        excedente = saidas - permitidas
        if excedente:
            falhas.append(
                f"service {nome} reaches the internet through {sorted(excedente)} "
                "with no declared authorization"
            )
        faltando = permitidas - saidas
        if faltando:
            falhas.append(
                f"service {nome} lost {sorted(faltando)}, which it needs to "
                + ", ".join(EGRESSO[rede] for rede in sorted(faltando))
            )

    if falhas:
        for falha in falhas:
            print(f"  {falha}", file=sys.stderr)
        return 1

    isoladas = sorted(set(redes) - set(EGRESSO))
    print(f"internal networks: {', '.join(isoladas)}")
    print(f"with egress: {', '.join(sorted(nome for nome in AUTORIZADOS))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
