#!/usr/bin/env bash
set -Eeuo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporario="$(mktemp -d /tmp/lucien-publisher-scan.XXXXXX)"
relatorios="$(mktemp -d /tmp/lucien-publisher-report.XXXXXX)"
docker_config="$(mktemp -d /tmp/lucien-publisher-docker.XXXXXX)"
printf '%s\n' '{"auths":{}}' > "$docker_config/config.json"

temporario_docker="$temporario"
relatorios_docker="$relatorios"
case "$(uname -s)" in
  MINGW*|MSYS*)
    # No Git Bash, converta somente a origem dos mounts. A conversão automática
    # também altera /src, /report e /etc/lucien, que pertencem ao contêiner.
    temporario_docker="$(cygpath -m "$temporario")"
    relatorios_docker="$(cygpath -m "$relatorios")"
    ;;
esac

limpar() {
  case "$temporario" in
    /tmp/lucien-publisher-scan.*) rm -rf -- "$temporario" ;;
    *) printf '%s\n' "Diretório temporário inesperado; limpeza recusada: $temporario" >&2 ;;
  esac
  case "$relatorios" in
    /tmp/lucien-publisher-report.*) rm -rf -- "$relatorios" ;;
    *) printf '%s\n' "Diretório de relatório inesperado; limpeza recusada: $relatorios" >&2 ;;
  esac
  case "$docker_config" in
    /tmp/lucien-publisher-docker.*) rm -rf -- "$docker_config" ;;
    *) printf '%s\n' "Diretório Docker inesperado; limpeza recusada: $docker_config" >&2 ;;
  esac
}
trap limpar EXIT

# O alvo da publicação não contém estado de laboratório. Copiar sem o histórico
# Git produz a visão exata do conteúdo que será enviado ao repositório remoto.
tar \
  --exclude='./.git' \
  -C "$raiz" -cf - . | tar -C "$temporario" -xf -

export DOCKER_CONFIG="$docker_config"
docker build --target runtime -t lucien-publisher-scanner "$raiz/secret-scanner"
set +e
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "$temporario_docker:/src:ro" \
  --volume "$relatorios_docker:/report" \
  --entrypoint gitleaks \
  lucien-publisher-scanner \
  dir /src \
  --config /etc/lucien/gitleaks.toml \
  --redact=100 \
  --exit-code=23 \
  --report-format=json \
  --report-path /report/gitleaks.json \
  --no-banner
resultado=$?
set -e

if [ "$resultado" -ne 0 ]; then
  if [ ! -s "$relatorios/gitleaks.json" ]; then
    printf '%s\n' \
      "Gitleaks falhou com código $resultado e não produziu relatório." >&2
    exit "$resultado"
  fi
  python3 - "$relatorios/gitleaks.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    findings = json.load(stream)
for rule_id, path, line in sorted(
    {(item["RuleID"], item["File"], item["StartLine"]) for item in findings}
):
    print(f"{rule_id}: {path}:{line}")
PY
  exit "$resultado"
fi

printf '%s\n' 'Exportação sem segredos detectados pelo ruleset do Lucien.'
