#!/bin/sh
set -eu

# O Git chama este helper; a credencial nunca integra URL ou argumentos do processo.
case "${1:-}" in
  *Username*|*username*)
    printf '%s\n' "${WIKI_REPOSITORY_USER:?}"
    ;;
  *Password*|*password*)
    if [ -n "${WIKI_REPOSITORY_TOKEN_FILE:-}" ]; then
      cat -- "$WIKI_REPOSITORY_TOKEN_FILE"
    else
      printf '%s\n' "${WIKI_REPOSITORY_TOKEN:?}"
    fi
    ;;
  *)
    exit 1
    ;;
esac

