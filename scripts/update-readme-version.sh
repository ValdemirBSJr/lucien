#!/usr/bin/env bash
# Faz a secao de download dos READMEs apontar para a release corrente.
#
# A versao aparece em tres lugares por README -- titulo da secao, o texto que
# diz o que a release oferece, e o `VERSION=` do bloco copiavel. Escrita a mao,
# ela ficou obsoleta em duas releases seguidas: quem chegava no repositorio era
# instruido a baixar a versao anterior, sem nenhum sinal disso.
#
# A fonte da verdade e a tag `v*` mais recente do repositorio, nao um numero
# digitado aqui. Sem rede: le as tags locais.
#
#   bash scripts/update-readme-version.sh              # deriva da ultima tag
#   VERSION=1.4.0 bash scripts/update-readme-version.sh
#   bash scripts/update-readme-version.sh --check      # so verifica, nao grava
set -Eeuo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Entra no diretorio em vez de passar o caminho em `git -C`: o verify.sh
# exporta MSYS_NO_PATHCONV=1, e sob ele o Git Bash nao traduz o caminho estilo
# Unix para o formato que o git nativo do Windows entende. O `git -C` falhava
# calado, o guard concluia "sem remoto" e o portao aprovava sempre -- inutil.
cd "$RAIZ" || exit 1
ARQUIVOS=("README.md" "README.en.md")

erro() { printf '\033[0;31mErro: %s\033[0m\n' "$1" >&2; exit 1; }

APENAS_VERIFICA=0
[[ "${1:-}" == "--check" ]] && APENAS_VERIFICA=1

versao_da_tag() {
  git tag --list 'v*' --sort=-v:refname 2>/dev/null \
    | head -1 | sed 's/^v//'
}

# Sem remoto as tags locais nao sao as releases publicadas -- o repositorio de
# desenvolvimento parou nas tags anteriores a separacao, e derivar delas
# apontaria os READMEs para uma versao que ninguem pode baixar. Mesma razao
# pela qual o portao de segredos do verify.sh se recusa a rodar sem remoto.
if [[ -z "${VERSION:-}" ]] && ! git remote get-url origin >/dev/null 2>&1; then
  printf 'Ignorado: sem remoto, as tags locais nao sao as releases publicadas.\n'
  printf 'Rode no repositorio publico, ou passe VERSION= explicitamente.\n'
  exit 0
fi

VERSION="${VERSION:-$(versao_da_tag)}"
[[ -n "$VERSION" ]] || erro 'nenhuma tag v* encontrada; rode git fetch --tags ou passe VERSION='
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || erro "versao invalida: $VERSION"

# As tres formas em que a versao aparece na secao de download. Conferir so o
# `VERSION=` nao basta: a primeira versao deste script consertava dois dos tres
# lugares e o --check aprovava, porque olhava exatamente o lugar que o proprio
# sed tinha acertado. A prosa com `v1.2.0` ficava para tras.
PADROES=(
  's/.*### \(Baixar o CLI\|Download CLI\) \([0-9]\+\.[0-9]\+\.[0-9]\+\).*/\2/p'
  's/.*`v\([0-9]\+\.[0-9]\+\.[0-9]\+\)`.*/\1/p'
  's/.*VERSION=\([0-9]\+\.[0-9]\+\.[0-9]\+\).*/\1/p'
)

divergentes=()
for arquivo in "${ARQUIVOS[@]}"; do
  caminho="$RAIZ/$arquivo"
  # A secao de download so existe no publisher: a raiz e ambiente de
  # desenvolvimento, onde ninguem baixa o CLI pronto. Ausencia nao e erro.
  [[ -f "$caminho" ]] || continue
  grep -q 'VERSION=[0-9]' "$caminho" || continue

  encontradas=()
  for padrao in "${PADROES[@]}"; do
    while IFS= read -r achado; do
      [[ -n "$achado" && "$achado" != "$VERSION" ]] && encontradas+=("$achado")
    done < <(sed -n "$padrao" "$caminho")
  done
  (( ${#encontradas[@]} == 0 )) && continue

  # Ordena e deduplica para a mensagem nao repetir a mesma versao tres vezes.
  atual="$(printf '%s\n' "${encontradas[@]}" | sort -u | paste -sd, -)"

  divergentes+=("$arquivo: $atual")
  if (( ! APENAS_VERIFICA )); then
    # Substitui por linha reconhecida, e nao por numero solto: um `s/1.2.0/`
    # global tambem trocaria a versao de uma dependencia que por acaso
    # coincidisse. So o \b final -- o inicial nao casaria em `v1.2.0`, porque
    # `v` e `1` sao os dois caracteres de palavra e nao ha fronteira entre eles.
    sed -i -E \
      -e "s/^(### (Baixar o CLI|Download CLI) )[0-9]+\.[0-9]+\.[0-9]+/\1$VERSION/" \
      -e "s/\`v[0-9]+\.[0-9]+\.[0-9]+\`/\`v$VERSION\`/g" \
      -e "s/^VERSION=[0-9]+\.[0-9]+\.[0-9]+/VERSION=$VERSION/" \
      "$caminho"
  fi
done

if (( ${#divergentes[@]} == 0 )); then
  printf 'READMEs ja apontam para %s.\n' "$VERSION"
  exit 0
fi

if (( APENAS_VERIFICA )); then
  printf 'A release corrente e %s, mas os READMEs apontam para:\n' "$VERSION"
  printf '  %s\n' "${divergentes[@]}"
  printf 'Rode: bash scripts/update-readme-version.sh\n'
  exit 1
fi

printf 'Atualizado para %s:\n' "$VERSION"
printf '  %s\n' "${divergentes[@]}"
