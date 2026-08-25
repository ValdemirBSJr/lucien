#!/usr/bin/env bash
# Restaura o banco do Hub a partir de uma cópia. OPERAÇÃO DESTRUTIVA.
#
# Substitui o conteúdo do banco em produção. Exige confirmação digitada e
# derruba o Hub e o worker antes, porque restaurar com eles escrevendo produz
# um estado que não corresponde nem à cópia nem ao que havia.
#
# Antes de usar em produção, prove a cópia:
#   scripts/test-restore.sh <arquivo>
set -euo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
BACKUP_ENCRYPT_KEY_FILE="${BACKUP_ENCRYPT_KEY_FILE:-}"

erro() { printf 'Erro: %s\n' "$1" >&2; exit 1; }

compose() {
  docker compose --env-file .env -f "$COMPOSE_FILE" "$@"
}

ARQUIVO="${1:-}"
[[ -n "$ARQUIVO" ]] || erro 'informe o arquivo: scripts/restore-db.sh <cópia>'
[[ -r "$ARQUIVO" ]] || erro "cópia ilegível: $ARQUIVO"
[[ -f .env ]] || erro 'arquivo .env ausente'

POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
POSTGRES_DB="${POSTGRES_DB:-lucien}"
POSTGRES_USER="${POSTGRES_USER:-lucien}"

cat >&2 <<AVISO

  Isto substitui o conteúdo de "$POSTGRES_DB" pela cópia
  $ARQUIVO

  Tudo publicado depois dessa cópia deixa de existir no banco. Os artefatos
  já gravados no Git ou no disco permanecem, e passarão a não ter Job
  correspondente -- eles continuam legíveis, mas não aparecerão no catálogo.

AVISO
printf 'Digite RESTAURAR para prosseguir: ' >&2
read -r confirmacao
[[ "$confirmacao" == "RESTAURAR" ]] || erro 'cancelado'

TRABALHO="$(mktemp -d)"
trap 'rm -rf -- "$TRABALHO"' EXIT
DUMP="$TRABALHO/lucien.dump"
if [[ "$ARQUIVO" == *.enc ]]; then
  [[ -n "$BACKUP_ENCRYPT_KEY_FILE" ]] || erro 'cópia cifrada exige BACKUP_ENCRYPT_KEY_FILE'
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -pass "file:$BACKUP_ENCRYPT_KEY_FILE" \
    -in "$ARQUIVO" -out "$DUMP" || erro 'falha ao decifrar a cópia'
else
  cp -- "$ARQUIVO" "$DUMP"
fi

printf 'Parando Hub e worker...\n'
compose stop hub upload-worker

printf 'Restaurando...\n'
# `--clean --if-exists` remove os objetos antigos antes de recriar. Sem isso a
# restauração falharia em cima do schema existente.
if ! compose exec -T postgres pg_restore \
  --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
  --clean --if-exists --no-owner --no-privileges \
  < "$DUMP"; then
  printf '\nA restauração falhou. Hub e worker seguem parados de propósito:\n' >&2
  printf 'subir sobre um banco meio restaurado é pior que ficar fora do ar.\n' >&2
  exit 1
fi

printf 'Subindo Hub e worker...\n'
compose up -d hub upload-worker

printf '\nRestauração concluída. Verifique antes de liberar o uso:\n'
printf '  docker compose --env-file .env -f %s logs --tail=30 hub\n' "$COMPOSE_FILE"
printf '  lucien auth status\n'
