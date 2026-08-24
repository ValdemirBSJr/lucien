#!/usr/bin/env bash
# Regera os arquivos de lock das dependências Python, com hash.
#
# As dependências diretas já estavam fixadas nos pyproject.toml, mas as
# transitivas não: `fastapi==0.116.1` arrasta starlette, pydantic, anyio e uma
# dúzia de outras, cada uma resolvida na hora do build. Duas construções da
# mesma tag podiam trazer bytes diferentes, e um pacote transitivo
# comprometido entraria sem ninguém notar.
#
# O lock carrega o hash de cada artefato, e o build instala com
# `--require-hashes`: se os bytes não baterem, a construção falha em vez de
# seguir com outra coisa.
#
# A resolução roda dentro da mesma imagem base da produção -- os wheels
# escolhidos dependem da plataforma e da versão do Python, então resolver no
# Windows produziria um lock que não descreve o que roda no servidor.
#
# Uso: scripts/atualizar-locks.sh [serviço ...]
set -euo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

IMAGEM_PY="python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91"
UV="uv==0.9.9"

erro() { printf 'Erro: %s\n' "$1" >&2; exit 1; }

compilar() {
  local diretorio="$1" comando="$2"
  printf '\n== %s\n' "$diretorio"
  docker run --rm \
    --mount "type=bind,src=$ROOT_DIR/$diretorio,dst=/trabalho" \
    --workdir /trabalho \
    "$IMAGEM_PY" \
    sh -euc "pip install --quiet --no-cache-dir $UV >/dev/null && $comando" \
    || erro "falha ao compilar as dependências de $diretorio"
}

# Serviços com pyproject.toml. `setuptools` entra no lock de propósito: a
# instalação do próprio pacote usa --no-build-isolation, então o backend de
# build precisa estar entre as dependências verificadas por hash, e não ser
# baixado sem verificação na hora do build.
projeto() {
  local diretorio="$1"
  compilar "$diretorio" "
    printf 'setuptools>=80.9.0\n' > /tmp/build.in
    uv pip compile pyproject.toml /tmp/build.in \
      --generate-hashes --quiet --output-file requirements.lock
    uv pip compile pyproject.toml /tmp/build.in --extra test \
      --generate-hashes --quiet --output-file requirements-test.lock
  "
}

# Serviços que declaram dependências em requirements.txt.
requisitos() {
  local diretorio="$1"
  compilar "$diretorio" "
    uv pip compile requirements.txt \
      --generate-hashes --quiet --output-file requirements.lock
    uv pip compile requirements.txt requirements-test.txt \
      --generate-hashes --quiet --output-file requirements-test.lock
  "
}

command -v docker >/dev/null 2>&1 || erro 'docker não encontrado'

ALVOS=("$@")
if [[ "${#ALVOS[@]}" -eq 0 ]]; then
  ALVOS=(backend runbook-viewer secret-scanner wiki-builder docs)
fi

for alvo in "${ALVOS[@]}"; do
  case "$alvo" in
    backend|runbook-viewer|secret-scanner) projeto "$alvo" ;;
    wiki-builder) requisitos "$alvo" ;;
    docs)
      compilar "." "
        uv pip compile requirements-docs.txt \
          --generate-hashes --quiet --output-file requirements-docs.lock
      "
      ;;
    *) erro "alvo desconhecido: $alvo" ;;
  esac
done

printf '\nLocks atualizados. Revise o diff antes de commitar:\n'
printf '  git diff -- "*.lock"\n'
