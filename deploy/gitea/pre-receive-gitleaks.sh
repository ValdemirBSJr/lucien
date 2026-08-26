#!/usr/bin/env bash
# Hook pre-receive do Gitea: recusa o push quando o Gitleaks detecta segredo.
#
# Esta é a única camada fora da fronteira de confiança do Hub. Conteúdo que
# chega por `lucien job sent` já passou por Gitleaks e DLP; uma correção feita à
# mão no repositório, ou um commit direto de qualquer origem, não passou por
# nada. O hook fecha esse caminho.
#
# Executa no servidor Gitea, no contexto do processo que recebe o push. Não
# depende do Hub e não faz rede.
#
# Instalação: consulte deploy/gitea/README-pre-receive.md
set -euo pipefail

GITLEAKS_BIN="${LUCIEN_GITLEAKS_BIN:-/usr/local/bin/gitleaks}"
GITLEAKS_CONFIG="${LUCIEN_GITLEAKS_CONFIG:-/etc/lucien/gitleaks.toml}"
# Segundos, inteiro: `gitleaks --timeout` nao aceita duracao com sufixo.
GITLEAKS_TIMEOUT="${LUCIEN_GITLEAKS_TIMEOUT:-60}"
ZERO='0000000000000000000000000000000000000000'

recusar() {
  printf '\n' >&2
  printf 'PUSH REJECTED — Gitleaks detected a possible secret.\n' >&2
  printf '%s\n' "$1" >&2
  printf '\n' >&2
  printf 'The content was NOT written. Remove the secret, rotate the exposed\n' >&2
  printf 'credential and redo the commit. Replace the value with a placeholder\n' >&2
  printf 'such as SUA_SENHA_AQUI before publishing.\n' >&2
  exit 1
}

falhar_fechado() {
  printf '\n' >&2
  printf 'PUSH REJECTED — secret scanning could not run.\n' >&2
  printf '%s\n' "$1" >&2
  printf 'We fail closed on purpose: no scan, no publication.\n' >&2
  exit 1
}

[[ -x "$GITLEAKS_BIN" ]] || falhar_fechado "gitleaks missing at $GITLEAKS_BIN"
[[ -r "$GITLEAKS_CONFIG" ]] || falhar_fechado "configuration missing at $GITLEAKS_CONFIG"

# O hook gerado pelo Gitea repassa o stdin recebido do git para cada script em
# hooks/pre-receive.d/ e recusa o push se algum sair diferente de zero. Cada
# linha traz: <antigo> <novo> <ref>.
while read -r antigo novo referencia; do
  # Linha vazia ou truncada nao descreve referencia; ignorar evita que o
  # `set -e` transforme um push legitimo em recusa.
  [[ -n "$antigo" && -n "$novo" && -n "$referencia" ]] || continue

  # Remocao de branch nao introduz conteudo novo.
  [[ "$novo" != "$ZERO" ]] || continue

  # `git log -p` cobre cada commit recebido, e nao apenas o diff liquido: um
  # segredo introduzido e removido dentro do mesmo push continua no historico
  # e precisa ser detectado. Em branch nova, `--not --all` limita aos commits
  # que o repositorio ainda nao possui.
  if [[ "$antigo" == "$ZERO" ]]; then
    intervalo=("$novo" --not --all)
  else
    intervalo=("${antigo}..${novo}")
  fi

  conteudo="$(git log -p --no-color --no-notes "${intervalo[@]}" 2>/dev/null || true)"

  # Sem conteudo nao ha o que varrer, e o Gitleaks sai com erro quando recebe
  # stdin vazio — o que derrubaria um push legitimo.
  [[ -n "$conteudo" ]] || continue

  # `set +e` e obrigatorio aqui: com errexit ligado, a atribuicao aborta o
  # script assim que o Gitleaks devolve 23, o `case` abaixo nunca roda e a
  # recusa acontece sem nenhuma mensagem para quem tentou o push.
  set +e
    saida="$(printf %s "$conteudo" | "$GITLEAKS_BIN" stdin --config="$GITLEAKS_CONFIG" --no-banner --no-color --redact=100 --verbose --exit-code=23 --timeout="$GITLEAKS_TIMEOUT" 2>&1)"
  status=$?
  set -e

  case "$status" in
    0) ;;
    23)
      # --verbose e o que faz o achado chegar a quem teve o push recusado.
      # Sem ele o gitleaks imprime apenas "leaks found: 1", e o operador fica
      # sem saber o que procurar num runbook de dezenas de blocos.
      #
      # --redact=100 continua valendo: o valor sai como REDACTED, e o que
      # aparece a mais e a regra que casou. Em modo stdin nao existe arquivo
      # nem linha -- a entrada e um fluxo, nao arquivos.
      recusar "Reference: $referencia"$'\n'"$saida"
      ;;
    *)
      falhar_fechado "gitleaks returned code $status on reference $referencia"$'\n'"$saida"
      ;;
  esac
done

exit 0
