#!/usr/bin/env python3
"""Classifica como cada ambiente usa `\\r`, sem carregar o que foi digitado.

O CLI converte todo `\\r` em quebra de linha. Isso esta certo para equipamento
de rede, que usa `\\r` puro como fim de linha, e errado para o readline, que o
usa para redesenhar a linha ao corrigir um comando -- e ai a versao errada e a
corrigida viram duas linhas no runbook.

Para separar os dois casos e preciso saber a FORMA real de cada ambiente. Este
script le o log bruto da sessao e emite so a classificacao: se o `\\r` vem
acompanhado de apagar-ate-o-fim-da-linha, se o texto depois repete o texto
antes, se e `\\r\\n`. A comparacao de conteudo acontece aqui dentro; o
relatorio carrega apenas o veredito e o comprimento.

O log bruto tem TUDO que passou pelo terminal, inclusive credencial inserida em
prompt. Este script nao o copia e nao imprime seu texto.

Uso:
  python3 collect-terminal-samples.py                # sessao mais recente
  python3 collect-terminal-samples.py CAMINHO.log
  python3 collect-terminal-samples.py --rotulo "jump -> OLT ZTE"

Rode APOS `lucien stop` e ANTES de `lucien upload`: o upload apaga o log.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ESC = 0x1B
JANELA = 24


def diretorios_de_estado() -> list[pathlib.Path]:
    """Onde o CLI guarda a sessao, na ordem em que ele mesmo decide.

    E XDG_STATE_HOME, nao XDG_CONFIG_HOME: veja config.StateDir() no Go. O
    perfil e o token ficam em ~/.config/lucien; a sessao e o log bruto ficam em
    ~/.local/state/lucien. Procurar no lugar errado foi o primeiro erro deste
    script.
    """

    candidatos: list[pathlib.Path] = []
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        candidatos.append(pathlib.Path(base) / "lucien")
    candidatos.append(pathlib.Path.home() / ".local" / "state" / "lucien")
    return candidatos


def log_mais_recente() -> tuple[pathlib.Path | None, list[str]]:
    """Devolve o log e, em caso de falha, tudo que foi tentado."""

    tentados: list[str] = []
    for estado in diretorios_de_estado():
        # A sessao registra o caminho exato do log. E mais confiavel que
        # adivinhar pelo nome, e continua valendo se o formato mudar.
        sessao = estado / "session.json"
        tentados.append(str(sessao))
        if sessao.is_file():
            try:
                dados = json.loads(sessao.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                dados = {}
            caminho = dados.get("log_path")
            if caminho and pathlib.Path(caminho).is_file():
                return pathlib.Path(caminho), tentados

        logs = estado / "logs"
        tentados.append(str(logs / "session-*.log"))
        if logs.is_dir():
            achados = sorted(
                logs.glob("session-*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if achados:
                return achados[0], tentados
    return None, tentados


def forma(trecho: bytes) -> str:
    """Descreve o trecho por controles literais; texto vira so o tamanho."""
    partes: list[str] = []
    corridos = 0

    def fechar() -> None:
        nonlocal corridos
        if corridos:
            partes.append(f"«{corridos} car»")
            corridos = 0

    i = 0
    while i < len(trecho):
        b = trecho[i]
        if b == ESC:
            fim = i + 1
            if fim < len(trecho) and trecho[fim] == ord("["):
                fim += 1
                while fim < len(trecho) and not (0x40 <= trecho[fim] <= 0x7E):
                    fim += 1
                fim += 1
            else:
                fim += 1
            fechar()
            sequencia = trecho[i:fim].decode("latin-1")
            partes.append("ESC" + sequencia[1:].replace("\x1b", ""))
            i = fim
            continue
        if b == 0x0D:
            fechar()
            partes.append("\\r")
        elif b == 0x0A:
            fechar()
            partes.append("\\n")
        elif b == 0x08:
            fechar()
            partes.append("\\b")
        elif b == 0x07:
            fechar()
            partes.append("\\a")
        else:
            corridos += 1
        i += 1
    fechar()
    return "".join(partes)


def apenas_texto(trecho: bytes) -> bytes:
    """Remove controles e sequencias, deixando o texto visivel."""
    saida = bytearray()
    i = 0
    while i < len(trecho):
        b = trecho[i]
        if b == ESC:
            i += 1
            if i < len(trecho) and trecho[i] == ord("["):
                i += 1
                while i < len(trecho) and not (0x40 <= trecho[i] <= 0x7E):
                    i += 1
            i += 1
            continue
        if b >= 0x20 and b != 0x7F:
            saida.append(b)
        i += 1
    return bytes(saida)


def classificar(dados: bytes) -> list[dict[str, object]]:
    achados: list[dict[str, object]] = []
    for pos, byte in enumerate(dados):
        if byte != 0x0D:
            continue
        antes = dados[max(0, pos - JANELA):pos]
        depois = dados[pos + 1:pos + 1 + JANELA]

        crlf = depois[:1] == b"\n"
        # `ESC[K` logo apos o retorno: assinatura de redesenho do readline.
        janela_curta = depois[:12]
        apaga = b"\x1b[K" in janela_curta or b"\x1b[0K" in janela_curta

        # O texto depois repete o INICIO DA LINHA atual? O redesenho reimprime
        # o prompt, entao a comparacao tem de partir do comeco da linha, nao do
        # comeco da janela -- senao compara a cauda do texto antigo com o
        # prompt novo e nunca casa. A comparacao acontece aqui; so o veredito
        # sai no relatorio.
        inicio = max(
            dados.rfind(b"\n", 0, pos) + 1,
            dados.rfind(b"\r", 0, pos) + 1,
        )
        t_linha = apenas_texto(dados[inicio:pos])
        t_depois = apenas_texto(depois)
        prefixo = 0
        for a, d in zip(t_linha, t_depois):
            if a != d:
                break
            prefixo += 1
        # Limiar frouxo acusa saida de equipamento: `Rule 5 permit ip` e
        # `Rule 10 deny ip` compartilham `Rule `, cinco caracteres, e viravam
        # "redesenho". Um redesenho de verdade reimprime o prompt inteiro,
        # entao o trecho repetido cobre boa parte da linha, nao um pedaco.
        repete = prefixo >= 8 and prefixo * 2 >= len(t_linha)

        achados.append({
            "pos": pos,
            "crlf": crlf,
            "apaga": apaga,
            "repete": repete,
            "prefixo": prefixo,
            "antes": forma(antes),
            "depois": forma(depois),
        })
    return achados


PROMPT = __import__("re").compile(
    r"^(?:\([^)\r\n]+\)[ \t]*)?[A-Za-z0-9._-]*[#$>][ \t]*(.*)$"
)


def linhas_de_comando(dados: bytes) -> list[dict[str, object]]:
    """Relaciona linhas com prompt consecutivas.

    O relatorio de `\\r` mostrou que os parciais do Tab sao linhas fisicas, e
    nao quadros de redesenho. Entao a evidencia que resta esta ENTRE as linhas:
    se uma e prefixo da seguinte, quantas linhas de saida houve no meio, e se a
    anterior trazia backspace ou bell -- que e o Cisco apagando e reclamando de
    completacao ambigua.

    Como sempre, so a forma sai daqui: comprimento e veredito, nunca o texto.
    """

    achados: list[dict[str, object]] = []
    bruto = dados.replace(b"\r\n", b"\n").split(b"\n")

    anterior: tuple[int, bytes, bool, bool] | None = None
    saida_no_meio = 0
    for indice, linha in enumerate(bruto):
        tem_bs = b"\x08" in linha
        tem_bell = b"\x07" in linha
        texto = apenas_texto(linha)
        m = PROMPT.match(texto.decode("latin-1"))
        if m is None:
            if texto.strip():
                saida_no_meio += 1
            continue

        comando = m.group(1).strip().encode("latin-1")
        if anterior is not None and comando:
            _, cmd_ant, bs_ant, bell_ant = anterior
            prefixo = cmd_ant and comando.startswith(cmd_ant)
            achados.append({
                "linha": indice + 1,
                "prefixo_do_seguinte": bool(prefixo),
                "len_anterior": len(cmd_ant),
                "len_atual": len(comando),
                "saida_no_meio": saida_no_meio,
                "anterior_tinha_backspace": bs_ant,
                "anterior_tinha_bell": bell_ant,
            })
        anterior = (indice, comando, tem_bs, tem_bell)
        saida_no_meio = 0
    return achados


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("log", nargs="?", help="caminho do log bruto da sessao")
    ap.add_argument("--rotulo", default="", help="de onde veio esta amostra")
    ap.add_argument("--limite", type=int, default=40, help="ocorrencias a listar")
    args = ap.parse_args()

    tentados: list[str] = []
    if args.log:
        caminho = pathlib.Path(args.log)
        tentados.append(args.log)
    else:
        caminho, tentados = log_mais_recente()

    if caminho is None or not caminho.is_file():
        print("Nenhum log de sessao encontrado. Procurei em:", file=sys.stderr)
        for tentativa in tentados:
            print(f"  {tentativa}", file=sys.stderr)
        print(
            "\nRode `lucien start`, reproduza o caso, `lucien stop`, e execute\n"
            "este script ANTES de `lucien upload`, que apaga o log.\n"
            "Se o caminho for outro, passe-o como argumento.",
            file=sys.stderr,
        )
        return 1

    print(f"log: {caminho}")

    dados = caminho.read_bytes()
    achados = classificar(dados)

    print(f"amostra: {args.rotulo or '(sem rotulo)'}")
    print(f"bytes no log: {len(dados)}")
    print(f"ocorrencias de \\r: {len(achados)}")
    print()

    crlf = sum(1 for a in achados if a["crlf"])
    apaga = sum(1 for a in achados if a["apaga"] and not a["crlf"])
    repete = sum(1 for a in achados if a["repete"] and not a["crlf"])
    puro = sum(
        1 for a in achados
        if not a["crlf"] and not a["apaga"] and not a["repete"]
    )
    print("resumo:")
    print(f"  \\r\\n (fim de linha comum)          {crlf}")
    print(f"  \\r + ESC[K (redesenho provavel)     {apaga}")
    print(f"  \\r + texto repetido (redesenho)     {repete}")
    print(f"  \\r isolado (fim de linha do device) {puro}")
    print()

    print("ocorrencias (texto substituido por «N car»):")
    for achado in achados[: args.limite]:
        etiquetas = []
        if achado["crlf"]:
            etiquetas.append("CRLF")
        if achado["apaga"]:
            etiquetas.append("ESC[K")
        if achado["repete"]:
            etiquetas.append(f"repete({achado['prefixo']})")
        if not etiquetas:
            etiquetas.append("isolado")
        print(f"  @{achado['pos']:<8} [{','.join(etiquetas)}]")
        print(f"      antes:  {achado['antes']}")
        print(f"      depois: {achado['depois']}")

    if len(achados) > args.limite:
        print(f"  ... mais {len(achados) - args.limite} nao listadas")

    pares = linhas_de_comando(dados)
    print()
    print("=== linhas com prompt, uma apos a outra")
    encadeados = [p for p in pares if p["prefixo_do_seguinte"]]
    print(f"  pares de comandos consecutivos:        {len(pares)}")
    print(f"  em que o anterior e prefixo do atual:  {len(encadeados)}")
    if encadeados:
        sem_saida = sum(1 for p in encadeados if p["saida_no_meio"] == 0)
        com_bs = sum(1 for p in encadeados if p["anterior_tinha_backspace"])
        com_bell = sum(1 for p in encadeados if p["anterior_tinha_bell"])
        print(f"    sem nenhuma saida entre os dois:     {sem_saida}")
        print(f"    anterior com backspace:              {com_bs}")
        print(f"    anterior com bell:                   {com_bell}")
        print()
        for p in encadeados[: args.limite]:
            marcas = []
            if p["saida_no_meio"] == 0:
                marcas.append("sem saida")
            else:
                marcas.append(f"{p['saida_no_meio']} linha(s) de saida")
            if p["anterior_tinha_backspace"]:
                marcas.append("\\b")
            if p["anterior_tinha_bell"]:
                marcas.append("\\a")
            print(
                f"    linha {p['linha']:<5} {p['len_anterior']:>3} car -> "
                f"{p['len_atual']:>3} car  [{', '.join(marcas)}]"
            )

    print()
    print("Nada do que voce digitou aparece acima. Pode enviar este relatorio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
