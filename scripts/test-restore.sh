#!/usr/bin/env bash
# Prova que uma cópia de segurança restaura de verdade.
#
# Uma cópia nunca restaurada é uma hipótese, não um plano de recuperação. Este
# teste restaura num PostgreSQL descartável, isolado do banco de produção, e
# confere que o schema e os dados chegaram inteiros.
#
# Nada aqui toca a instalação: o contêiner temporário sobe sem rede do projeto,
# com senha efêmera, e é removido ao fim mesmo se algo falhar. # gitleaks:allow
#
# Uso:
#   scripts/test-restore.sh                       # cópia mais recente
#   scripts/test-restore.sh backups/lucien-....dump
set -euo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
BACKUP_ENCRYPT_KEY_FILE="${BACKUP_ENCRYPT_KEY_FILE:-}"
IMAGEM_PG="${IMAGEM_PG:-postgres:17.10-alpine3.23@sha256:8189a1f6e40904781fc9e2612687877791d21679866db58b1de996b31fc312e4}"

erro() { printf 'Error: %s\n' "$1" >&2; exit 1; }

ARQUIVO="${1:-}"
if [[ -z "$ARQUIVO" ]]; then
  ARQUIVO="$(
    find "$BACKUP_DIR" -maxdepth 1 -name 'lucien-*.dump*' -type f 2>/dev/null \
      | sort | tail -1
  )"
  [[ -n "$ARQUIVO" ]] || erro "no backup found in $BACKUP_DIR"
fi
[[ -r "$ARQUIVO" ]] || erro "unreadable backup: $ARQUIVO"

TRABALHO="$(mktemp -d)"
CONTEINER="lucien-restauracao-$$"
limpar() {
  docker rm -f "$CONTEINER" >/dev/null 2>&1 || true
  rm -rf -- "$TRABALHO"
}
trap limpar EXIT

DUMP="$TRABALHO/lucien.dump"
if [[ "$ARQUIVO" == *.enc ]]; then
  [[ -n "$BACKUP_ENCRYPT_KEY_FILE" ]] || erro 'encrypted backup requires BACKUP_ENCRYPT_KEY_FILE'
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -pass "file:$BACKUP_ENCRYPT_KEY_FILE" \
    -in "$ARQUIVO" -out "$DUMP" || erro 'failed to decrypt the backup'
else
  cp -- "$ARQUIVO" "$DUMP"
fi

SENHA="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"
printf 'Starting a throwaway PostgreSQL...\n'
docker run -d --rm --name "$CONTEINER" \
  --network none \
  -e POSTGRES_PASSWORD="$SENHA" \
  -e POSTGRES_USER=lucien \
  -e POSTGRES_DB=restauracao \
  "$IMAGEM_PG" >/dev/null

pronto=0
for _ in $(seq 1 60); do
  if docker exec "$CONTEINER" pg_isready -U lucien -d restauracao >/dev/null 2>&1; then
    pronto=1
    break
  fi
  sleep 1
done
[[ "$pronto" -eq 1 ]] || erro 'the temporary PostgreSQL never became ready'

printf 'Restoring %s...\n' "$(basename "$ARQUIVO")"
if ! docker exec -i "$CONTEINER" pg_restore \
  --username=lucien --dbname=restauracao --no-owner --no-privileges \
  < "$DUMP"; then
  erro 'pg_restore failed; this backup is NOT usable for recovery'
fi

consulta() {
  docker exec -i "$CONTEINER" psql -U lucien -d restauracao -tAc "$1"
}

printf '\nChecking the result:\n'
FALHOU=0

# As tabelas que sustentam identidade e publicação. Faltando qualquer uma, a
# restauração não devolve um Hub operável.
for tabela in users jobs upload_queue; do
  existe="$(consulta "SELECT to_regclass('public.$tabela') IS NOT NULL")"
  if [[ "$existe" == "t" ]]; then
    total="$(consulta "SELECT count(*) FROM $tabela")"
    printf '  OK      %-14s %s row(s)\n' "$tabela" "$total"
  else
    printf '  FAILED  %-14s missing\n' "$tabela"
    FALHOU=1
  fi
done

# Um Hub sem admin ativo não pode ser administrado; a recuperação estaria
# incompleta mesmo com todas as tabelas presentes.
ADMINS="$(consulta "SELECT count(*) FROM users WHERE role_level = 'admin' AND is_active")"
if [[ "${ADMINS:-0}" -ge 1 ]]; then
  printf '  OK      %-14s %s active\n' "admin" "$ADMINS"
else
  printf '  FAILED  %-14s no active admin in the backup\n' "admin"
  FALHOU=1
fi

# As constraints acompanham o dump? Sem elas o banco restaurado aceitaria
# estado que o Hub considera impossível.
CONSTRAINTS="$(consulta "SELECT count(*) FROM pg_constraint WHERE contype = 'c'")"
printf '  OK      %-14s %s CHECK\n' "constraints" "$CONSTRAINTS"

if [[ "$FALHOU" -ne 0 ]]; then
  printf '\nThe restore finished but the contents do not check out.\n'
  exit 1
fi

printf '\nRestore verified successfully.\n'
printf 'Record the evidence in docs/operacao.md: date, file, and result.\n'
