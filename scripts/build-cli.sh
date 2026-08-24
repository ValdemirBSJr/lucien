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
  printf 'Erro: %s\n' "$1" >&2
  exit 1
}

[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || erro 'VERSION contém caracteres inválidos'
command -v docker >/dev/null 2>&1 || erro 'Docker não encontrado'
docker info >/dev/null 2>&1 || erro 'daemon Docker indisponível'
command -v tar >/dev/null 2>&1 || erro 'tar não encontrado'
command -v sha256sum >/dev/null 2>&1 || erro 'sha256sum não encontrado'
[[ -f "$ROOT_DIR/LICENSE" ]] || erro 'LICENSE ausente'
[[ -f "$ROOT_DIR/NOTICE" ]] || erro 'NOTICE ausente'
[[ -f "$ROOT_DIR/THIRD-PARTY-NOTICES.txt" ]] || \
  erro 'THIRD-PARTY-NOTICES.txt ausente; execute scripts/atualizar-avisos-cli.sh'

mkdir -p "$OUTPUT_DIR"
mkdir -p "$BUILD_CACHE_DIR/build" "$BUILD_CACHE_DIR/mod"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$ROOT_DIR/cli,dst=/src,readonly" \
  --mount "type=bind,src=$TEMP_DIR,dst=/out" \
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
      mkdir -p "/out/${package_name}"
      CGO_ENABLED=0 GOOS="$target_os" GOARCH="$target_arch" \
        go build -trimpath -buildvcs=false \
        -ldflags="-s -w -X github.com/lucien-runbook/lucien/cmd.version=$VERSION" \
        -o "/out/${package_name}/lucien" .
      chmod 0755 "/out/${package_name}/lucien"
    done
  '

for target in $TARGETS; do
  target_os=${target%/*}
  target_arch=${target#*/}
  package_name="lucien_${VERSION}_${target_os}_${target_arch}"
  package_dir="$TEMP_DIR/$package_name"
  archive="$OUTPUT_DIR/$package_name.tar.gz"

  cat > "$package_dir/LEIA-ME.txt" <<EOF
Lucien CLI para $target_os/$target_arch

Este pacote contém somente o binário do cliente. Não contém token, certificado ou
configuração do Hub. Verifique o SHA-256 antes de instalar. Configure API_HOST e
TLS_CA_FILE no ambiente do operador e distribua somente a CA pública do Hub.

Os pacotes macOS são cross-compilados e não são assinados nem notarizados.
As licenças do Lucien e das dependências compiladas acompanham este pacote.
EOF

  install -m 0644 "$ROOT_DIR/LICENSE" "$package_dir/LICENSE"
  install -m 0644 "$ROOT_DIR/NOTICE" "$package_dir/NOTICE"
  install -m 0644 "$ROOT_DIR/THIRD-PARTY-NOTICES.txt" \
    "$package_dir/THIRD-PARTY-NOTICES.txt"

  tar -C "$TEMP_DIR" -czf "$archive" "$package_name"
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

printf 'Pacotes gerados em %s\n' "$OUTPUT_DIR"
printf 'Checksums: %s\n' "$checksum_file"
