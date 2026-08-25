#!/usr/bin/env bash
set -Eeuo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
imagem="${GO_IMAGE:-golang:1.25.12-alpine3.23@sha256:cc985ef6f9c3bf9ece7488129c9abe0a150388ccdfa428d886fc709dca0b230a}"
versao_ferramenta="v1.6.0"
destino="${THIRD_PARTY_NOTICES_FILE:-$raiz/THIRD-PARTY-NOTICES.txt}"
temporario="$(mktemp -d /tmp/lucien-go-licenses.XXXXXX)"

limpar() {
  case "$temporario" in
    /tmp/lucien-go-licenses.*) rm -rf -- "$temporario" ;;
    *) printf 'Diretório temporário inesperado: %s\n' "$temporario" >&2 ;;
  esac
}
trap limpar EXIT

origem_cli="$raiz/cli"
temporario_docker="$temporario"
case "$(uname -s)" in
  MINGW*|MSYS*)
    origem_cli="$(cygpath -m "$origem_cli")"
    temporario_docker="$(cygpath -m "$temporario")"
    ;;
esac

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "$origem_cli:/src:ro" \
  --volume "$temporario_docker:/work" \
  --workdir /src \
  --env HOME=/tmp \
  --env GOBIN=/tmp/bin \
  "$imagem" \
  sh -euc "
    go install github.com/google/go-licenses@${versao_ferramenta}
    /tmp/bin/go-licenses report \
      --ignore github.com/lucien-runbook/lucien ./... > /work/report.csv
    /tmp/bin/go-licenses save \
      --ignore github.com/lucien-runbook/lucien \
      --save_path /work/licenses ./...
  "

if grep -Fq ',Unknown,Unknown' "$temporario/report.csv"; then
  printf '%s\n' 'Licença desconhecida detectada; arquivo não atualizado.' >&2
  grep -F ',Unknown,Unknown' "$temporario/report.csv" >&2
  exit 1
fi

parcial="$destino.parcial"
{
  printf '%s\n\n' 'THIRD-PARTY NOTICES FOR THE LUCIEN CLI'
  printf '%s\n' \
    'Generated from cli/go.mod and cli/go.sum with go-licenses v1.6.0.' \
    'The report below identifies the dependencies linked into the CLI binary.' \
    'The corresponding license notices and texts follow the report.'
  printf '\n%s\n' 'DEPENDENCY REPORT'
  LC_ALL=C sort "$temporario/report.csv"

  while IFS= read -r arquivo; do
    relativo="${arquivo#"$temporario/licenses/"}"
    componente="${relativo%/*}"
    nome_licenca="${relativo##*/}"
    printf '\n%s\n' \
      '===============================================================================' \
      "Component: $componente" \
      "License file: $nome_licenca" \
      '===============================================================================' \
      ''
    sed -e 's/\r$//' "$arquivo"
  done < <(find "$temporario/licenses" -type f -print | LC_ALL=C sort)
} > "$parcial"

mv -f -- "$parcial" "$destino"
printf 'Avisos de terceiros atualizados em %s\n' "$destino"
