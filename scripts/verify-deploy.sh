#!/usr/bin/env bash
# Confere o que está NO AR contra o que está no repositório.
#
# `scripts/verify.sh` valida o código. Este valida a implantação, que é
# outra coisa. Numa noite os dois divergiram em quatro pontos e nenhum portão
# viu:
#
#   - `docker-compose.local.yml` era anterior à segmentação de rede, então o
#     banco continuou com saída para a internet depois de a mudança "subir";
#   - a imagem do wiki-builder perdeu um arquivo que o serviço lê a cada ciclo;
#   - o fonte do secret-scanner no servidor era anterior à reescrita;
#   - a imagem do secret-scanner idem, mascarada por um healthcheck que
#     apontava para uma rota que existia nas duas versões.
#
# Rode no host do Hub, a partir da raiz da instalação, depois de qualquer
# implantação. Não altera nada.
set -uo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"

declare -a FALHAS=()
declare -a AVISOS=()

compose() {
  docker compose --env-file .env -f "$COMPOSE_FILE" "$@"
}

titulo() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
ok() { printf '\033[0;32m  OK      %s\033[0m\n' "$1"; }
falha() {
  printf '\033[0;31m  FAILED  %s\033[0m\n' "$1"
  FALHAS+=("$1")
}
aviso() {
  printf '\033[0;33m  WARN    %s\033[0m\n' "$1"
  AVISOS+=("$1")
}

[[ -f .env ]] || { printf 'Error: run this from the installation root\n' >&2; exit 1; }

POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
POSTGRES_DB="${POSTGRES_DB:-lucien}"
POSTGRES_USER="${POSTGRES_USER:-lucien}"

# --- 1. configuração em uso --------------------------------------------------

titulo "Configuration"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  falha "$COMPOSE_FILE does not exist"
elif cmp -s -- docker-compose.yml "$COMPOSE_FILE"; then
  ok "$COMPOSE_FILE matches the repository baseline"
else
  falha "$COMPOSE_FILE differs from docker-compose.yml; run deploy/install-hub.sh --refresh-compose"
fi

# --- 2. fonte no servidor ----------------------------------------------------
#
# Um marcador por serviço: um símbolo que só existe na versão atual. Fonte
# defasado no servidor faz qualquer reconstrução produzir a imagem errada, e
# foi assim que o scanner ficou semanas para trás.

titulo "Source on the server"

conferir_fonte() {
  local servico="$1" arquivo="$2" marcador="$3"
  if [[ ! -f "$arquivo" ]]; then
    aviso "$servico: $arquivo is missing (service not installed here?)"
    return
  fi
  if grep -q -- "$marcador" "$arquivo"; then
    ok "$servico: source contains $marcador"
  else
    falha "$servico: $arquivo does not contain $marcador; the source on the server is stale"
  fi
}

conferir_fonte "hub" "backend/app/infrastructure/database.py" "operational_counters"
conferir_fonte "secret-scanner" "secret-scanner/app/main.py" "SCANNER_MAX_CONCURRENCY"
conferir_fonte "wiki-builder" "wiki-builder/app/main.py" "ARQUIVOS_DA_IMPRESSAO"
conferir_fonte "runbook-viewer" "runbook-viewer/app/security.py" "extra_domains"

# --- 3. imagem em execução ---------------------------------------------------
#
# Fonte certo não prova imagem certa. A pergunta aqui é feita ao processo que
# está atendendo, não ao disco.

titulo "Running image"

# `docker compose ps -q` variou de comportamento entre ambientes durante o
# desenvolvimento deste script, e devolveu resultado nao vazio para servico que
# nao estava de pe -- produzindo um OK falso, exatamente o defeito que este
# arquivo existe para pegar. `exec` de um comando trivial e inequivoco: so tem
# sucesso se houver contêiner em execucao.
alcancavel() {
  compose exec -T "$1" sh -c 'exit 0' >/dev/null 2>&1
}

rota_viva() {
  local servico="$1" url="$2"
  if ! alcancavel "$servico"; then
    aviso "$servico: not running"
    return
  fi
  local codigo
  codigo="$(
    compose exec -T "$servico" python -c "
import sys, urllib.request
try:
    print(urllib.request.urlopen('$url', timeout=5).status)
except Exception as erro:
    print(getattr(erro, 'code', 0))
" 2>/dev/null | tr -d '\r'
  )"
  if [[ "$codigo" == "200" ]]; then
    ok "$servico: $url answers 200"
  else
    falha "$servico: $url answered '$codigo'; the running image is not built from this source"
  fi
}

rota_viva "secret-scanner" "http://localhost:8090/ready"

# O wiki-builder não expõe HTTP. A pergunta equivalente é se os arquivos que
# ele lê a cada ciclo estão dentro da imagem.
if alcancavel wiki-builder; then
  resposta="$(
    compose exec -T wiki-builder python -c "
from pathlib import Path
from app.main import ARQUIVOS_DA_IMPRESSAO
faltando = [str(r) for r in ARQUIVOS_DA_IMPRESSAO if not (Path('/app') / r).is_file()]
print('COMPLETO' if not faltando else 'FALTA ' + ','.join(faltando))
" 2>/dev/null | tr -d '\r'
  )"
  if [[ "$resposta" == "COMPLETO" ]]; then
    ok "wiki-builder: fingerprint files present in the image"
  elif [[ -z "$resposta" ]]; then
    falha "wiki-builder: could not inspect the image"
  else
    falha "wiki-builder: $resposta"
  fi
else
  aviso "wiki-builder: not running"
fi

# --- 4. banco ----------------------------------------------------------------

titulo "Database"

esperadas="$(find backend/migrations -name '*.sql' -type f 2>/dev/null | wc -l | tr -d ' ')"
if ! alcancavel postgres; then
  aviso "postgres: not running"
  registradas="ignorado"
else
  registradas="$(
    compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -tAc 'SELECT count(*) FROM schema_migrations' 2>/dev/null | tr -d '\r'
  )"
fi
if [[ "$registradas" == "ignorado" ]]; then
  :
elif [[ -z "$registradas" ]]; then
  falha "could not read schema_migrations"
elif [[ "$registradas" -ge "$esperadas" ]]; then
  ok "migrations recorded: $registradas of $esperadas files"
else
  falha "schema_migrations has $registradas, the repository has $esperadas migrations"
fi

# --- 5. segmentação, medida ---------------------------------------------------
#
# Ler o arquivo do Compose não prova nada: era exatamente o que estava certo no
# repositório e errado no ar. Aqui a pergunta é feita abrindo conexão.

titulo "Network segmentation"

sem_saida() {
  local servico="$1"
  if ! alcancavel "$servico"; then
    aviso "$servico: not running"
    return
  fi
  # IP e não nome: o que `internal: true` remove é rota, não resolução. E o
  # comando serve tanto ao alpine do postgres, que traz nc e não traz Python,
  # quanto às imagens Python, que trazem o contrário.
  #
  # 1.1.1.1 é ALVO DE SONDA, não dado do ambiente: precisa ser um endereço
  # público que realmente responde. Substituí-lo por uma faixa privada faz a
  # conexão falhar sempre, e então esta função aprova até um contêiner com
  # internet total -- o oposto do que ela existe para medir. Não sanitizar.
  if compose exec -T "$servico" sh -c '
if command -v nc >/dev/null 2>&1; then
  nc -z -w 5 1.1.1.1 443
else
  python -c "import socket;socket.create_connection((\"1.1.1.1\",443),5)"
fi' >/dev/null 2>&1; then
    falha "$servico reaches the internet and should not"
  else
    ok "$servico has no route to the internet"
  fi
}

sem_saida "postgres"
sem_saida "upload-worker"
sem_saida "secret-scanner"

# --- veredito ----------------------------------------------------------------

printf '\n\033[1m== Result\033[0m\n'
if [[ "${#AVISOS[@]}" -gt 0 ]]; then
  printf '%d warning(s); a service that is down is not a mismatch.\n' "${#AVISOS[@]}"
fi
if [[ "${#FALHAS[@]}" -gt 0 ]]; then
  for item in "${FALHAS[@]}"; do
    printf '\033[0;31m  FAILED  %s\033[0m\n' "$item"
  done
  printf '\n%d mismatch(es) between the repository and what is running.\n' "${#FALHAS[@]}"
  exit 1
fi
printf '\nWhat is running matches the repository.\n'
