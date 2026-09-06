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

# Redes que existem para que uma porta publicada funcione.
#
# O Docker não publica porta de contêiner que só esteja em rede `internal`:
# aceita a declaração e não faz nada. O portal local e o Nginx da wiki ficaram
# inalcançáveis por isso, cada um declarando uma porta que nunca subiu.
#
# "Ingresso" aqui é rótulo, não garantia: no Docker não existe meia rede, e
# uma bridge não-interna dá entrada E saída. O que a separação compra é
# concentrar essa saída em um serviço que só encaminha, mantendo fora dela
# quem lê runbook ou fala com o banco.
INGRESSO = {
    "viewer_ingress": "publish the local portal through viewer-proxy",
    "wiki_ingress": "publish the compact wiki through wiki-static",
}

# Quem tem permissão de sair, e por qual rede. Serviço fora deste mapa não
# pode tocar em nenhuma rede de egresso.
AUTORIZADOS = {
    "hub": {"git_egress"},
    "slm": {"slm_egress"},
    "wiki-builder": {"wiki_egress"},
}

# Quem pode receber conexão de fora. Mesma lógica do mapa acima: serviço novo
# numa rede de ingresso é decisão a tomar, não detalhe a herdar.
AUTORIZADOS_INGRESSO = {
    "viewer-proxy": {"viewer_ingress"},
    "wiki-static": {"wiki_ingress"},
}


def internas_de(redes: dict) -> set[str]:
    """Nomes das redes marcadas `internal: true` no Compose renderizado."""
    return {
        nome
        for nome, definicao in redes.items()
        if bool((definicao or {}).get("internal"))
    }


def _portas(definicao: dict) -> str:
    """Portas publicadas, como o operador as escreveu no arquivo."""
    rotulos = []
    for porta in definicao.get("ports") or []:
        if isinstance(porta, dict):
            publicada = porta.get("published") or porta.get("target")
            rotulos.append(f"{publicada}->{porta.get('target')}")
        else:
            rotulos.append(str(porta))
    return ", ".join(rotulos)


def main() -> int:
    dados = json.load(sys.stdin)
    redes = dados.get("networks", {})
    servicos = dados.get("services", {})
    falhas: list[str] = []

    externas = set(EGRESSO) | set(INGRESSO)

    for nome, definicao in redes.items():
        interna = bool((definicao or {}).get("internal"))
        if nome in EGRESSO and interna:
            falhas.append(f"network {nome} is internal but needs to {EGRESSO[nome]}")
        if nome in INGRESSO and interna:
            falhas.append(
                f"network {nome} is internal, so no published port on it works; "
                f"it exists to {INGRESSO[nome]}"
            )
        if nome not in externas and not interna:
            falhas.append(
                f"network {nome} is neither internal nor declared as egress or "
                "ingress; add the reason to EGRESSO/INGRESSO or mark it "
                "internal: true"
            )

    for nome, definicao in servicos.items():
        definicao = definicao or {}
        anexadas = set(definicao.get("networks") or {})
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

        entradas = anexadas & set(INGRESSO)
        permitidas_entrada = AUTORIZADOS_INGRESSO.get(nome, set())
        excedente_entrada = entradas - permitidas_entrada
        if excedente_entrada:
            falhas.append(
                f"service {nome} accepts connections through "
                f"{sorted(excedente_entrada)} with no declared authorization"
            )
        faltando_entrada = permitidas_entrada - entradas
        if faltando_entrada:
            falhas.append(
                f"service {nome} lost {sorted(faltando_entrada)}, which it needs "
                "to " + ", ".join(INGRESSO[r] for r in sorted(faltando_entrada))
            )

        # A invariante que faltava. Porta publicada em serviço que só toca
        # rede interna é aceita pelo Compose, não sobe, e não avisa: o
        # `docker compose ps` mostra a porta sem mapeamento e o serviço fica
        # inalcançável. Foi assim que o portal local e a wiki compacta
        # ficaram fora do ar sem nada reclamar.
        if definicao.get("ports") and not (anexadas - internas_de(redes)):
            falhas.append(
                f"service {nome} publishes {_portas(definicao)} but is only on "
                f"internal networks {sorted(anexadas)}; Docker accepts the "
                "declaration and never publishes it"
            )

    if falhas:
        for falha in falhas:
            print(f"  {falha}", file=sys.stderr)
        return 1

    isoladas = sorted(set(redes) - set(EGRESSO) - set(INGRESSO))
    print(f"internal networks: {', '.join(isoladas)}")
    print(f"with egress: {', '.join(sorted(nome for nome in AUTORIZADOS))}")
    print(f"with ingress: {', '.join(sorted(AUTORIZADOS_INGRESSO))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
