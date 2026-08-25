#!/usr/bin/env bash
# Cópia de segurança do banco do Hub, verificada e com retenção.
#
# O dump carrega hashes de credencial, a fila cifrada e o conteúdo dos Jobs.
# Ele é material sensível: o diretório nasce 0700 e o arquivo 0600, e cifrar
# em repouso é uma opção -- ver BACKUP_ENCRYPT_KEY_FILE abaixo.
#
# Uso:
#   scripts/backup-db.sh                    # destino padrão
#   BACKUP_DIR=/mnt/backup scripts/backup-db.sh
set -euo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
# Quantas cópias manter. Zero desliga a remoção -- útil enquanto se decide a
# política, mas o disco é finito e alguém precisa olhar.
BACKUP_RETENTION="${BACKUP_RETENTION:-14}"
# Arquivo com a senha usada para cifrar o dump. Sem ele, o dump fica em texto # gitleaks:allow
# claro e a proteção é apenas a permissão do sistema de arquivos.
BACKUP_ENCRYPT_KEY_FILE="${BACKUP_ENCRYPT_KEY_FILE:-}"

erro() { printf 'Erro: %s\n' "$1" >&2; exit 1; }
aviso() { printf '\033[0;33mAviso: %s\033[0m\n' "$1" >&2; }

compose() {
  docker compose --env-file .env -f "$COMPOSE_FILE" "$@"
}

[[ -f .env ]] || erro "arquivo .env ausente; execute a partir da raiz da instalação"
[[ -f "$COMPOSE_FILE" ]] || erro "$COMPOSE_FILE ausente"
[[ "$BACKUP_RETENTION" =~ ^[0-9]+$ ]] || erro 'BACKUP_RETENTION deve ser inteiro'

POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
POSTGRES_DB="${POSTGRES_DB:-lucien}"
POSTGRES_USER="${POSTGRES_USER:-lucien}"

install -d -m 0700 "$BACKUP_DIR"
CARIMBO="$(date -u +%Y%m%dT%H%M%SZ)"
DESTINO="$BACKUP_DIR/lucien-$CARIMBO.dump"

printf 'Gerando cópia de %s...\n' "$POSTGRES_DB"
# Formato custom: permite restauração seletiva e verificação do índice sem
# reexecutar o SQL inteiro.
if ! compose exec -T postgres pg_dump \
  --username="$POSTGRES_USER" \
  --dbname="$POSTGRES_DB" \
  --format=custom \
  --compress=6 \
  > "$DESTINO.parcial"; then
  rm -f "$DESTINO.parcial"
  erro 'pg_dump falhou; nenhuma cópia foi gravada'
fi
chmod 0600 "$DESTINO.parcial"

# Verificação: um dump que não pode ser lido não é cópia de segurança, é a
# ilusão de uma. Ler o índice prova que o arquivo chegou íntegro.
printf 'Verificando o índice do arquivo...\n'
if ! compose exec -T postgres pg_restore --list < "$DESTINO.parcial" > /dev/null; then
  rm -f "$DESTINO.parcial"
  erro 'o arquivo gerado não pôde ser lido; cópia descartada'
fi

if [[ -n "$BACKUP_ENCRYPT_KEY_FILE" ]]; then
  [[ -r "$BACKUP_ENCRYPT_KEY_FILE" ]] || erro "chave ilegível: $BACKUP_ENCRYPT_KEY_FILE"
  command -v openssl >/dev/null 2>&1 || erro 'openssl não encontrado para cifrar'
  openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
    -pass "file:$BACKUP_ENCRYPT_KEY_FILE" \
    -in "$DESTINO.parcial" -out "$DESTINO.enc"
  chmod 0600 "$DESTINO.enc"
  rm -f "$DESTINO.parcial"
  DESTINO="$DESTINO.enc"
else
  mv "$DESTINO.parcial" "$DESTINO"
  aviso 'dump em texto claro; defina BACKUP_ENCRYPT_KEY_FILE para cifrar em repouso'
fi

TAMANHO="$(du -h "$DESTINO" | cut -f1)"
printf 'Cópia concluída: %s (%s)\n' "$DESTINO" "$TAMANHO"

if [[ "$BACKUP_RETENTION" -gt 0 ]]; then
  # Remove as mais antigas somente depois de a nova estar verificada no disco:
  # apagar antes deixaria uma janela sem cópia nenhuma.
  mapfile -t ANTIGAS < <(
    find "$BACKUP_DIR" -maxdepth 1 -name 'lucien-*.dump*' -type f \
      | sort -r | tail -n +$((BACKUP_RETENTION + 1))
  )
  for arquivo in "${ANTIGAS[@]:-}"; do
    [[ -n "$arquivo" ]] || continue
    printf 'Removendo cópia antiga: %s\n' "$(basename "$arquivo")"
    rm -f -- "$arquivo"
  done
fi

printf '\nProve esta copia antes de confiar nela:\n'
printf '  BACKUP_DIR=%s scripts/test-restore.sh %s\n' "$BACKUP_DIR" "$DESTINO"
