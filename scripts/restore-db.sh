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

erro() { printf 'Error: %s\n' "$1" >&2; exit 1; }

compose() {
  docker compose --env-file .env -f "$COMPOSE_FILE" "$@"
}

ARQUIVO="${1:-}"
[[ -n "$ARQUIVO" ]] || erro 'name the file: scripts/restore-db.sh <backup>'
[[ -r "$ARQUIVO" ]] || erro "unreadable backup: $ARQUIVO"
[[ -f .env ]] || erro 'no .env file'

POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
POSTGRES_DB="${POSTGRES_DB:-lucien}"
POSTGRES_USER="${POSTGRES_USER:-lucien}"

cat >&2 <<AVISO

  This replaces the contents of "$POSTGRES_DB" with the backup
  $ARQUIVO

  Anything published after that backup stops existing in the database.
  Artifacts already written to Git or to disk stay where they are and end up
  without a matching Job -- still readable, but absent from the catalog.

AVISO
printf 'Type RESTORE to proceed: ' >&2
read -r confirmacao
[[ "$confirmacao" == "RESTORE" ]] || erro 'canceled'

TRABALHO="$(mktemp -d)"
trap 'rm -rf -- "$TRABALHO"' EXIT
DUMP="$TRABALHO/lucien.dump"
if [[ "$ARQUIVO" == *.enc ]]; then
  [[ -n "$BACKUP_ENCRYPT_KEY_FILE" ]] || erro 'encrypted backup requires BACKUP_ENCRYPT_KEY_FILE'
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -pass "file:$BACKUP_ENCRYPT_KEY_FILE" \
    -in "$ARQUIVO" -out "$DUMP" || erro 'failed to decrypt the backup'
else
  cp -- "$ARQUIVO" "$DUMP"
fi

printf 'Stopping Hub and worker...\n'
compose stop hub upload-worker

printf 'Restoring...\n'
# `--clean --if-exists` remove os objetos antigos antes de recriar. Sem isso a
# restauração falharia em cima do schema existente.
if ! compose exec -T postgres pg_restore \
  --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
  --clean --if-exists --no-owner --no-privileges \
  < "$DUMP"; then
  printf '\nRestore failed. Hub and worker stay down on purpose:\n' >&2
  printf 'serving from a half-restored database is worse than being offline.\n' >&2
  exit 1
fi

printf 'Starting Hub and worker...\n'
compose up -d hub upload-worker

printf '\nRestore complete. Check these before letting people back in:\n'
printf '  docker compose --env-file .env -f %s logs --tail=30 hub\n' "$COMPOSE_FILE"
printf '  lucien auth status\n'
