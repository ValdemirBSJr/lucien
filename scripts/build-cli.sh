#!/usr/bin/env bash
set -euo pipefail

# Gera pacotes do Lucien via Docker para Linux e macOS, sem incluir segredos ou CAs.
ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/dist}"
VERSION="${VERSION:-dev}"
GO_IMAGE="${GO_IMAGE:-golang:1.25.12-alpine3.23@sha256:cc985ef6f9c3bf9ece7488129c9abe0a150388ccdfa428d886fc709dca0b230a}"
TARGETS="linux/amd64 linux/arm64 darwin/amd64 darwin/arm64"
BUILD_CACHE_DIR="${LUCIEN_BUILD_CACHE_DIR:-/tmp/lucien-go-cache-$(id -u)}"

erro() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || erro 'VERSION contains invalid characters'
command -v docker >/dev/null 2>&1 || erro 'Docker not found'
docker info >/dev/null 2>&1 || erro 'Docker daemon unavailable'
command -v tar >/dev/null 2>&1 || erro 'tar not found'
command -v sha256sum >/dev/null 2>&1 || erro 'sha256sum not found'
[[ -f "$ROOT_DIR/LICENSE" ]] || erro 'LICENSE is missing'
[[ -f "$ROOT_DIR/NOTICE" ]] || erro 'NOTICE is missing'
[[ -f "$ROOT_DIR/THIRD-PARTY-NOTICES.txt" ]] || \
  erro 'THIRD-PARTY-NOTICES.txt is missing; run scripts/update-cli-notices.sh'

mkdir -p "$OUTPUT_DIR"
mkdir -p "$BUILD_CACHE_DIR/build" "$BUILD_CACHE_DIR/mod"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

# O pacote inteiro é montado DENTRO do contêiner, e só o .tar.gz pronto cruza
# para o host.
#
# Antes o binário era escrito no bind mount, recebia `chmod 0755` ali, e o tar
# rodava no host. Em Linux funciona; no Windows o bit de execução não persiste
# através do bind mount, o chmod vira no-op e o tar grava 0644. O resultado é o
# pior tipo de defeito: build verde, checksum correto, testes passando, e um
# pacote em que `./lucien` responde "Permission denied" para quem baixou.
#
# Aconteceu de verdade nas releases 1.2.0 e 1.3.0. A 1.1.9, construída em
# Linux, saiu certa -- o que escondeu o problema até alguém instalar do zero.
#
# Dentro do contêiner o /tmp é um sistema de arquivos real: o modo gravado no
# tar é o modo que foi escrito, em qualquer sistema operacional hospedeiro.
docker run --rm \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$ROOT_DIR/cli,dst=/src,readonly" \
  --mount "type=bind,src=$TEMP_DIR,dst=/out" \
  --mount "type=bind,src=$ROOT_DIR/LICENSE,dst=/licencas/LICENSE,readonly" \
  --mount "type=bind,src=$ROOT_DIR/NOTICE,dst=/licencas/NOTICE,readonly" \
  --mount "type=bind,src=$ROOT_DIR/THIRD-PARTY-NOTICES.txt,dst=/licencas/THIRD-PARTY-NOTICES.txt,readonly" \
  --mount "type=bind,src=$BUILD_CACHE_DIR/build,dst=/go-build" \
  --mount "type=bind,src=$BUILD_CACHE_DIR/mod,dst=/go-mod" \
  --workdir /src \
  --env HOME=/tmp \
  --env GOCACHE=/go-build \
  --env GOMODCACHE=/go-mod \
  --env "VERSION=$VERSION" \
  --env "TARGETS=$TARGETS" \
  "$GO_IMAGE" \
  sh -euc '
    go test ./... -count=1 -timeout=30s
    for target in $TARGETS; do
      target_os=${target%/*}
      target_arch=${target#*/}
      package_name="lucien_${VERSION}_${target_os}_${target_arch}"
      package_dir="/tmp/pacote/${package_name}"
      mkdir -p "$package_dir"
      CGO_ENABLED=0 GOOS="$target_os" GOARCH="$target_arch" \
        go build -trimpath -buildvcs=false \
        -ldflags="-s -w -X github.com/lucien-runbook/lucien/cmd.version=$VERSION" \
        -o "$package_dir/lucien" .
      chmod 0755 "$package_dir/lucien"

      cp /licencas/LICENSE /licencas/NOTICE \
        /licencas/THIRD-PARTY-NOTICES.txt "$package_dir/"

      cat > "$package_dir/LEIA-ME.txt" <<LEIAME
Lucien CLI para ${target_os}/${target_arch}

Este pacote contém somente o binário do cliente. Não contém token, certificado ou
configuração do Hub. Verifique o SHA-256 antes de instalar. Configure API_HOST e
TLS_CA_FILE no ambiente do operador e distribua somente a CA pública do Hub.

Os pacotes macOS são cross-compilados e não são assinados nem notarizados.
As licenças do Lucien e das dependências compiladas acompanham este pacote.
LEIAME

      chmod 0644 "$package_dir/LICENSE" "$package_dir/NOTICE" \
        "$package_dir/THIRD-PARTY-NOTICES.txt" "$package_dir/LEIA-ME.txt"

      tar -C /tmp/pacote -czf "/out/${package_name}.tar.gz" "$package_name"
    done
  '

# O contêiner escreveu os arquivos prontos; ao host resta conferir o que saiu.
#
# A verificação existe porque o defeito anterior era silencioso: nada no build
# acusava, e o pacote só falhava na mão de quem baixou.
for target in $TARGETS; do
  target_os=${target%/*}
  target_arch=${target#*/}
  package_name="lucien_${VERSION}_${target_os}_${target_arch}"
  archive="$TEMP_DIR/$package_name.tar.gz"

  [[ -f "$archive" ]] || erro "package was not produced: $package_name.tar.gz"

  # Caminho relativo de propósito: no Git Bash do Windows o `C:` de um caminho
  # absoluto é lido pelo tar como nome de host remoto, e ele tenta conectar.
  modo="$(cd "$TEMP_DIR" && tar -tvzf "$package_name.tar.gz" \
    | awk -v alvo="$package_name/lucien" '$NF == alvo { print $1 }')"
  [[ -n "$modo" ]] || erro "binary missing from $package_name.tar.gz"
  case "$modo" in
    -rwxr-xr-x) ;;
    *) erro "binary in $package_name.tar.gz is not executable: $modo" ;;
  esac

  mv -- "$archive" "$OUTPUT_DIR/$package_name.tar.gz"
done

checksum_file="$OUTPUT_DIR/lucien_${VERSION}_SHA256SUMS"
: > "$checksum_file"
for target in $TARGETS; do
  target_os=${target%/*}
  target_arch=${target#*/}
  archive_name="lucien_${VERSION}_${target_os}_${target_arch}.tar.gz"
  (
    cd "$OUTPUT_DIR"
    sha256sum "$archive_name"
  ) > "$OUTPUT_DIR/$archive_name.sha256"
  cat "$OUTPUT_DIR/$archive_name.sha256" >> "$checksum_file"
done

printf 'Packages written to %s\n' "$OUTPUT_DIR"
printf 'Checksums: %s\n' "$checksum_file"
