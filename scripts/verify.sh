#!/usr/bin/env bash
# Portões de qualidade do Lucien, na mesma forma que o CI executa.
#
# Existe para ser rodado ANTES de copiar arquivos para o servidor. O fluxo de
# implantação é manual, então um CI que só dispara no push não protegeria o
# momento em que a mudança realmente chega à produção.
#
# Cada portão é independente: todos rodam, e o script só decide o veredito no
# fim. Parar no primeiro erro esconderia os demais e custaria uma rodada
# inteira para descobrir o próximo.
set -uo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

# O Git Bash reescreve argumentos que parecem caminho POSIX, transformando
# `--workdir /src` em `C:/Program Files/Git/src`. Desligar a conversao deixa o
# script utilizavel tanto no MSYS quanto no WSL, que e onde ele roda.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

SOMENTE="${1:-}"
declare -a APROVADOS=()
declare -a REPROVADOS=()
declare -a IGNORADOS=()

azul() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }

portao() {
  local nome="$1"
  shift
  if [[ -n "$SOMENTE" && "$nome" != *"$SOMENTE"* ]]; then
    return 0
  fi
  azul "$nome"
  if "$@"; then
    APROVADOS+=("$nome")
  else
    REPROVADOS+=("$nome")
  fi
}

pular() {
  IGNORADOS+=("$1 — $2")
  printf '\033[0;33m-- %s skipped: %s\033[0m\n' "$1" "$2"
}

tem() { command -v "$1" >/dev/null 2>&1; }

# --- imagens de teste -------------------------------------------------------

imagem_de_teste() {
  local nome="$1" tag="$2"
  shift 2
  docker build --target test -t "$tag" "$@" >/dev/null || return 1
  docker run --rm "$tag"
}

portao_backend() {
  imagem_de_teste backend lucien-hub-test backend
}

# Migracao e o codigo que roda uma vez, com o banco de producao na mesa.
# SQLite nao serve de prova: os marcadores consultam o catalogo do PostgreSQL.
# Por isso este portao sobe um PostgreSQL descartavel, sem rede do projeto e
# com senha efemera, e o remove ao fim mesmo se algo falhar. # gitleaks:allow
portao_migracoes() {
  local rede="lucien-migracoes-$$" pg="lucien-migracoes-pg-$$" senha estado=1 # gitleaks:allow
  senha="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 18)"
  docker build --target test -t lucien-hub-test backend >/dev/null || return 1
  docker network create "$rede" >/dev/null || return 1
  if docker run -d --rm --name "$pg" --network "$rede"       -e POSTGRES_PASSWORD="$senha"       -e POSTGRES_USER=lucien       -e POSTGRES_DB=postgres       "$IMAGEM_PG" >/dev/null; then
    local pronto=0
    for _ in $(seq 1 60); do
      if docker exec "$pg" pg_isready -U lucien >/dev/null 2>&1; then
        pronto=1
        break
      fi
      sleep 1
    done
    if [[ "$pronto" -eq 1 ]]; then
      docker run --rm --network "$rede"         -e POSTGRES_TEST_DATABASE_URL="postgresql+asyncpg://lucien:$senha@$pg:5432/postgres"         lucien-hub-test python -m pytest -q tests/test_migrations.py
      estado=$?
    else
      printf 'the throwaway PostgreSQL never became ready\n' >&2
    fi
  fi
  docker rm -f "$pg" >/dev/null 2>&1 || true
  docker network rm "$rede" >/dev/null 2>&1 || true
  return "$estado"
}

portao_viewer() {
  # O contexto é a raiz porque o portal incorpora o logo e os contratos do Hub.
  docker build --target test -t lucien-viewer-test -f runbook-viewer/Dockerfile . >/dev/null || return 1
  docker run --rm --entrypoint python lucien-viewer-test -m pytest -q
}

portao_wiki() {
  imagem_de_teste wiki-builder lucien-wiki-test wiki-builder
}

portao_scanner() {
  imagem_de_teste secret-scanner lucien-scanner-test secret-scanner
}

# --- CLI --------------------------------------------------------------------

portao_cli() {
  local falhou=0
  (
    cd cli || exit 1
    local desformatado
    desformatado="$(gofmt -l .)"
    if [[ -n "$desformatado" ]]; then
      printf 'gofmt rejected:\n%s\n' "$desformatado" >&2
      exit 1
    fi
    go vet ./... || exit 1
    go test ./... || exit 1
  ) || falhou=1
  return "$falhou"
}

# --- scripts shell ----------------------------------------------------------

portao_shell() {
  local falhou=0
  local arquivo
  local -a arquivos_shell=()
  while IFS= read -r arquivo; do
    bash -n "$arquivo" || falhou=1
  done < <(find deploy scripts -name '*.sh' -type f)
  if tem shellcheck; then
    # SC1091: `source` de arquivo gerado na instalação, ausente em análise.
    mapfile -d '' -t arquivos_shell < <(
      find deploy scripts -name '*.sh' -type f -print0
    )
    shellcheck --severity=warning --exclude=SC1091 \
      "${arquivos_shell[@]}" || falhou=1
  else
    printf 'shellcheck not installed; only bash -n ran\n' >&2
  fi
  return "$falhou"
}

# --- Compose e documentação -------------------------------------------------

portao_compose() {
  docker compose --env-file .env.example config --quiet || return 1
  # A segmentacao so e visivel com todos os perfis ativos: cada perfil traz
  # servicos diferentes, e "quem alcanca a internet" vale para a uniao deles.
  docker compose --env-file .env.example     --profile server --profile consolidated --profile local-viewer     --profile gitea-compact --profile tools     config --format json     | docker run --rm -i         --mount "type=bind,src=$ROOT_DIR/scripts,dst=/scripts,readonly"         --network none         "$IMAGEM_LINT" python3 /scripts/verify-networks.py
}

portao_docs() {
  # Em conteiner, e nao pelo ambiente virtual local: o mesmo comando do CI,
  # sem depender de como cada maquina montou o venv. Um shim criado no WSL
  # nao executa a partir do Windows, e a divergencia aparecia como falha do
  # projeto em vez de do ambiente.
  docker run --rm \
    --mount "type=bind,src=$ROOT_DIR,dst=/docs,readonly" \
    --workdir /docs \
    "$IMAGEM_LINT" \
    sh -euc 'pip install --quiet --require-hashes --requirement requirements-docs.lock >/dev/null && mkdocs build --strict --site-dir /tmp/site >/dev/null'
}

# --- lint e tipagem Python --------------------------------------------------

portao_lint_python() {
  docker run --rm \
    --mount "type=bind,src=$ROOT_DIR/backend,dst=/src/backend,readonly" \
    --mount "type=bind,src=$ROOT_DIR/runbook-viewer,dst=/src/runbook-viewer,readonly" \
    --mount "type=bind,src=$ROOT_DIR/wiki-builder,dst=/src/wiki-builder,readonly" \
    --mount "type=bind,src=$ROOT_DIR/secret-scanner,dst=/src/secret-scanner,readonly" \
    --workdir /src \
    "$IMAGEM_LINT" \
    sh -euc 'pip install --quiet ruff==0.14.5 >/dev/null && ruff check --no-cache .'
}

portao_tipagem() {
  # So o backend por enquanto: e onde porta e implementacao podem divergir em
  # silencio, que foi o defeito real. Estender aos demais e trabalho proprio.
  docker build --target test -t lucien-hub-test backend >/dev/null || return 1
  # `python -m mypy` porque a imagem roda sem privilegio e o script instalado
  # cai fora do PATH.
  docker run --rm lucien-hub-test sh -euc     'pip install --quiet mypy==1.18.2 >/dev/null && python -m mypy --config-file pyproject.toml app'
}

IMAGEM_PG="postgres:17.10-alpine3.23@sha256:8189a1f6e40904781fc9e2612687877791d21679866db58b1de996b31fc312e4"
IMAGEM_LINT="python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91"

# --- execução ---------------------------------------------------------------

tem docker || { printf 'Docker is required for the image gates.\n' >&2; exit 2; }

portao "backend" portao_backend
portao "migrations" portao_migracoes
portao "runbook-viewer" portao_viewer
portao "wiki-builder" portao_wiki
portao "secret-scanner" portao_scanner

if tem go; then
  portao "cli" portao_cli
else
  pular "cli" "go not found"
fi

portao "shell" portao_shell
portao "compose" portao_compose

portao "docs" portao_docs

portao "lint-python" portao_lint_python
portao "types" portao_tipagem

# --- veredito ---------------------------------------------------------------

printf '\n\033[1m== Result\033[0m\n'
for nome in "${APROVADOS[@]:-}"; do
  [[ -n "$nome" ]] && printf '\033[0;32m  OK      %s\033[0m\n' "$nome"
done
for nome in "${IGNORADOS[@]:-}"; do
  [[ -n "$nome" ]] && printf '\033[0;33m  SKIPPED %s\033[0m\n' "$nome"
done
for nome in "${REPROVADOS[@]:-}"; do
  [[ -n "$nome" ]] && printf '\033[0;31m  FAILED  %s\033[0m\n' "$nome"
done

if [[ "${#REPROVADOS[@]}" -gt 0 ]]; then
  printf '\n%d gate(s) failed. Nothing should be published.\n' "${#REPROVADOS[@]}"
  exit 1
fi
printf '\nAll gates passed.\n'
