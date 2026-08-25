#!/usr/bin/env bash
set -Eeuo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$raiz"

# Estado efêmero nunca fica dentro da árvore publicável. O Docker config usa o
# perfil do usuário; as chaves existem somente em volume Docker dedicado.
estado_base="${LUCIEN_PUBLISHER_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/lucien-publisher}"
export DOCKER_CONFIG="$estado_base/docker"
install -d -m 0700 "$DOCKER_CONFIG"
if [ ! -s "$DOCKER_CONFIG/config.json" ]; then
  printf '%s\n' '{"auths":{}}' > "$DOCKER_CONFIG/config.json"
  chmod 0600 "$DOCKER_CONFIG/config.json"
fi

compose=(
  docker compose
  --env-file testes/wiki-local/.env.demo
  -f docker-compose.yml
  -f docker-compose.build.yml
  -f testes/wiki-local/docker-compose.demo.yml
)
wiki_url="http://127.0.0.1:${WIKI_DEMO_PORT:-19092}"

printf '%s\n' 'Construindo o wiki-builder e o gerador de certificados...'
"${compose[@]}" --profile gitea-compact --profile tools build wiki-builder certgen

printf '%s\n' 'Gerando uma CA efêmera exclusiva do laboratório...'
"${compose[@]}" --profile gitea-compact --profile tools \
  down --volumes --remove-orphans
"${compose[@]}" --profile tools run --rm certgen
printf '%s\n' 'Subindo a origem Git HTTPS, o builder e a página local...'
"${compose[@]}" --profile gitea-compact up -d --force-recreate \
  demo-cert-permissions demo-git-init wiki-source wiki-volume-init \
  wiki-builder wiki-static

printf '%s\n' 'Aguardando a primeira release válida...'
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --output /dev/null \
    "$wiki_url/"; then
    printf 'Wiki disponível em %s\n' "$wiki_url"
    "${compose[@]}" --profile gitea-compact ps
    exit 0
  fi
  sleep 2
done

printf '%s\n' 'A wiki não ficou pronta no tempo esperado.' >&2
"${compose[@]}" --profile gitea-compact logs --tail=100 \
  demo-git-init wiki-source wiki-builder wiki-static >&2
exit 1
