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
    "git_egress": "publicar no GitHub ou Gitea",
    "slm_egress": "baixar o modelo com `ollama pull`",
    "wiki_egress": "clonar o repositório da wiki",
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
            falhas.append(f"rede {nome} está interna, mas precisa {EGRESSO[nome]}")
        if nome not in EGRESSO and not interna:
            falhas.append(
                f"rede {nome} não é interna e não está declarada como egresso; "
                "acrescente a justificativa em EGRESSO ou marque internal: true"
            )

    for nome, definicao in servicos.items():
        anexadas = set((definicao or {}).get("networks") or {})
        saidas = anexadas & set(EGRESSO)
        permitidas = AUTORIZADOS.get(nome, set())
        excedente = saidas - permitidas
        if excedente:
            falhas.append(
                f"serviço {nome} alcança a internet por {sorted(excedente)} "
                "sem autorização declarada"
            )
        faltando = permitidas - saidas
        if faltando:
            falhas.append(
                f"serviço {nome} perdeu {sorted(faltando)}, de que depende para "
                + ", ".join(EGRESSO[rede] for rede in sorted(faltando))
            )

    if falhas:
        for falha in falhas:
            print(f"  {falha}", file=sys.stderr)
        return 1

    isoladas = sorted(set(redes) - set(EGRESSO))
    print(f"redes internas: {', '.join(isoladas)}")
    print(f"com saída: {', '.join(sorted(nome for nome in AUTORIZADOS))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
