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
  printf 'PUSH RECUSADO — Gitleaks detectou possivel segredo.\n' >&2
  printf '%s\n' "$1" >&2
  printf '\n' >&2
  printf 'O conteudo NAO foi gravado. Remova o segredo, rotacione a credencial\n' >&2
  printf 'exposta e refaca o commit. Substitua o valor por um placeholder como\n' >&2
  printf 'SUA_SENHA_AQUI antes de publicar.\n' >&2
  exit 1
}

falhar_fechado() {
  printf '\n' >&2
  printf 'PUSH RECUSADO — o secret scanning nao pode ser executado.\n' >&2
  printf '%s\n' "$1" >&2
  printf 'Falhamos fechado de proposito: sem varredura, nao ha publicacao.\n' >&2
  exit 1
}

[[ -x "$GITLEAKS_BIN" ]] || falhar_fechado "gitleaks ausente em $GITLEAKS_BIN"
[[ -r "$GITLEAKS_CONFIG" ]] || falhar_fechado "configuracao ausente em $GITLEAKS_CONFIG"

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
    saida="$(printf %s "$conteudo" | "$GITLEAKS_BIN" stdin --config="$GITLEAKS_CONFIG" --no-banner --no-color --redact=100 --exit-code=23 --timeout="$GITLEAKS_TIMEOUT" 2>&1)"
  status=$?
  set -e

  case "$status" in
    0) ;;
    23)
      # --redact=100 garante que o achado nunca imprime o valor do segredo;
      # apenas arquivo, linha e regra chegam ao operador.
      recusar "Referencia: $referencia"$'\n'"$saida"
      ;;
    *)
      falhar_fechado "gitleaks retornou codigo $status na referencia $referencia"$'\n'"$saida"
      ;;
  esac
done

exit 0
