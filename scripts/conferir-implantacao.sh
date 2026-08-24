#!/usr/bin/env bash
# Confere o que está NO AR contra o que está no repositório.
#
# `scripts/verificar.sh` valida o código. Este valida a implantação, que é
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
  printf '\033[0;31m  FALHOU  %s\033[0m\n' "$1"
  FALHAS+=("$1")
}
aviso() {
  printf '\033[0;33m  AVISO   %s\033[0m\n' "$1"
  AVISOS+=("$1")
}

[[ -f .env ]] || { printf 'Erro: execute a partir da raiz da instalação\n' >&2; exit 1; }

POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
POSTGRES_DB="${POSTGRES_DB:-lucien}"
POSTGRES_USER="${POSTGRES_USER:-lucien}"

# --- 1. configuração em uso --------------------------------------------------

titulo "Configuração"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  falha "$COMPOSE_FILE não existe"
elif cmp -s -- docker-compose.yml "$COMPOSE_FILE"; then
  ok "$COMPOSE_FILE idêntico à base do repositório"
else
  falha "$COMPOSE_FILE difere de docker-compose.yml; rode deploy/install-hub.sh --refresh-compose"
fi

# --- 2. fonte no servidor ----------------------------------------------------
#
# Um marcador por serviço: um símbolo que só existe na versão atual. Fonte
# defasado no servidor faz qualquer reconstrução produzir a imagem errada, e
# foi assim que o scanner ficou semanas para trás.

titulo "Fonte no servidor"

conferir_fonte() {
  local servico="$1" arquivo="$2" marcador="$3"
  if [[ ! -f "$arquivo" ]]; then
    aviso "$servico: $arquivo ausente (serviço não instalado aqui?)"
    return
  fi
  if grep -q -- "$marcador" "$arquivo"; then
    ok "$servico: fonte contém $marcador"
  else
    falha "$servico: $arquivo não contém $marcador; o fonte no servidor está defasado"
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

titulo "Imagem em execução"

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
    aviso "$servico: não está em execução"
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
    ok "$servico: $url responde 200"
  else
    falha "$servico: $url respondeu '$codigo'; a imagem em execução não é a do fonte"
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
    ok "wiki-builder: arquivos da impressão digital presentes na imagem"
  elif [[ -z "$resposta" ]]; then
    falha "wiki-builder: não foi possível inspecionar a imagem"
  else
    falha "wiki-builder: $resposta"
  fi
else
  aviso "wiki-builder: não está em execução"
fi

# --- 4. banco ----------------------------------------------------------------

titulo "Banco"

esperadas="$(find backend/migrations -name '*.sql' -type f 2>/dev/null | wc -l | tr -d ' ')"
if ! alcancavel postgres; then
  aviso "postgres: não está em execução"
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
  falha "não foi possível ler schema_migrations"
elif [[ "$registradas" -ge "$esperadas" ]]; then
  ok "migrações registradas: $registradas de $esperadas arquivos"
else
  falha "schema_migrations tem $registradas, o repositório tem $esperadas migrações"
fi

# --- 5. segmentação, medida ---------------------------------------------------
#
# Ler o arquivo do Compose não prova nada: era exatamente o que estava certo no
# repositório e errado no ar. Aqui a pergunta é feita abrindo conexão.

titulo "Segmentação de rede"

sem_saida() {
  local servico="$1"
  if ! alcancavel "$servico"; then
    aviso "$servico: não está em execução"
    return
  fi
  # IP e não nome: o que `internal: true` remove é rota, não resolução. E o
  # comando serve tanto ao alpine do postgres, que traz nc e não traz Python,
  # quanto às imagens Python, que trazem o contrário.
  if compose exec -T "$servico" sh -c '
if command -v nc >/dev/null 2>&1; then
  nc -z -w 5 10.200.0.1 443
else
  python -c "import socket;socket.create_connection((\"10.200.0.1\",443),5)"
fi' >/dev/null 2>&1; then
    falha "$servico alcança a internet e não deveria"
  else
    ok "$servico sem rota para a internet"
  fi
}

sem_saida "postgres"
sem_saida "upload-worker"
sem_saida "secret-scanner"

# --- veredito ----------------------------------------------------------------

printf '\n\033[1m== Resultado\033[0m\n'
if [[ "${#AVISOS[@]}" -gt 0 ]]; then
  printf '%d aviso(s); serviço fora do ar não é divergência.\n' "${#AVISOS[@]}"
fi
if [[ "${#FALHAS[@]}" -gt 0 ]]; then
  for item in "${FALHAS[@]}"; do
    printf '\033[0;31m  FALHOU  %s\033[0m\n' "$item"
  done
  printf '\n%d divergência(s) entre o repositório e o que está no ar.\n' "${#FALHAS[@]}"
  exit 1
fi
printf '\nO que está no ar corresponde ao repositório.\n'
