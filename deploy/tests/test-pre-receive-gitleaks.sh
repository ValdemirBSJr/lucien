#!/usr/bin/env bash
# Uso: bash deploy/tests/test-pre-receive-gitleaks.sh deploy/gitea/pre-receive-gitleaks.sh
# Exercita o hook num repositorio bare real, com gitleaks simulado.
# O stub detecta a string SEGREDO_DE_TESTE, imitando --exit-code=23.
set -uo pipefail

RAIZ="$(mktemp -d)"
# Resolvido antes de qualquer cd: os `cp` seguintes rodam de outros diretorios.
HOOK="$(cd "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
[ -r "$HOOK" ] || { printf 'hook not found: %s
' "$1" >&2; exit 1; }
trap 'rm -rf "$RAIZ"' EXIT

mkdir -p "$RAIZ/bin"
cat > "$RAIZ/bin/gitleaks" <<'STUB'
#!/usr/bin/env bash
# Valida os flags como o binario real faria: um stub permissivo aprovaria
# argumento que o gitleaks recusa, como --timeout com sufixo de duracao.
verboso=0
for arg in "$@"; do
  case "$arg" in
    --timeout=*)
      valor="${arg#--timeout=}"
      case "$valor" in
        *[!0-9]*|"")
          echo "Error: invalid argument \"$valor\" for --timeout flag" >&2
          exit 1
          ;;
      esac
      ;;
    --redact=*)
      valor="${arg#--redact=}"
      case "$valor" in *[!0-9]*|"") echo "Error: --redact invalid" >&2; exit 1;; esac
      ;;
    --exit-code=*)
      valor="${arg#--exit-code=}"
      case "$valor" in *[!0-9]*|"") echo "Error: --exit-code invalid" >&2; exit 1;; esac
      ;;
    --verbose|-v) verboso=1 ;;
    stdin|--config=*|--no-banner|--no-color) ;;
    *) echo "Error: unknown flag: $arg" >&2; exit 1 ;;
  esac
done
c="$(cat)"
[ -n "$c" ] || { echo "Error: entrada vazia" >&2; exit 1; }
if printf '%s' "$c" | grep -q 'SEGREDO_DE_TESTE'; then
  # O detalhe so sai com --verbose, como no binario real. Assim, se o hook
  # perder o flag, a afirmacao do avaliar() falha em vez de seguir passando.
  if [ "$verboso" -eq 1 ]; then
    printf 'Finding:     SEGREDO_DE_TESTE REDACTED
'
    printf 'Secret:      REDACTED
'
    printf 'RuleID:      lucien-teste-sentinela
'
    printf '
'
  fi
  printf 'WRN leaks found: 1
'
  exit 23
fi
exit 0
STUB
chmod +x "$RAIZ/bin/gitleaks"
printf 'config de teste\n' > "$RAIZ/gitleaks.toml"

export LUCIEN_GITLEAKS_BIN="$RAIZ/bin/gitleaks"
export LUCIEN_GITLEAKS_CONFIG="$RAIZ/gitleaks.toml"

git init -q --bare "$RAIZ/remoto.git"
install -d -m 0755 "$RAIZ/remoto.git/hooks/pre-receive.d"
# Reproduz o delegador que o Gitea gera.
cat > "$RAIZ/remoto.git/hooks/pre-receive" <<'GITEA'
#!/usr/bin/env bash
data=$(cat)
exitcodes=""
hookname=$(basename $0)
GIT_DIR=${GIT_DIR:-$(dirname $0)/..}
for hook in ${GIT_DIR}/hooks/${hookname}.d/*; do
  test -x "${hook}" && test -f "${hook}" || continue
  echo "${data}" | "${hook}"
  exitcodes="${exitcodes} $?"
done
for i in ${exitcodes}; do
  [ ${i} -eq 0 ] || exit ${i}
done
GITEA
cp "$HOOK" "$RAIZ/remoto.git/hooks/pre-receive.d/gitleaks"
chmod +x "$RAIZ/remoto.git/hooks/pre-receive" "$RAIZ/remoto.git/hooks/pre-receive.d/gitleaks"

git clone -q "$RAIZ/remoto.git" "$RAIZ/clone" 2>/dev/null
cd "$RAIZ/clone" || exit 1
git config user.email teste@lucien.local
git config user.name Teste

FALHAS=0

# Recusa sem mensagem e defeito: quem tentou o push precisa saber o motivo.
# Conferir so o codigo de saida deixou passar um bug em que o `set -e` abortava
# o script antes de imprimir qualquer coisa.
avaliar() {
  local rotulo="$1" esperado="$2" rc="$3" saida="${4:-}"
  local erro=''

  [ "$rc" -eq "$esperado" ] || erro="rc=$rc expected=$esperado"
  if [ "$esperado" -ne 0 ] && ! printf %s "$saida" | grep -q "PUSH REJECTED"; then
    erro="${erro:+$erro; }rejected without a message"
  fi
  # Recusar sem dizer o que casou manda o operador procurar as cegas. E o
  # detalhe so existe porque o hook passa --verbose -- esta afirmacao e o que
  # impede o flag de ser removido em silencio.
  if [ "$esperado" -ne 0 ] && ! printf %s "$saida" | grep -q 'RuleID:'; then
    erro="${erro:+$erro; }rejected without naming the rule"
  fi

  if [ -z "$erro" ]; then
    printf 'OK    %-46s rc=%s\n' "$rotulo" "$rc"
  else
    printf 'FAIL  %-46s %s\n' "$rotulo" "$erro"
    FALHAS=$((FALHAS + 1))
  fi
}

# 1. Primeira branch, commit raiz limpo -> deve passar.
printf 'procedimento limpo\n' > a.md
git add a.md && git commit -q -m "raiz limpa"
saida="$(git push -q origin HEAD:refs/heads/main 2>&1)"
avaliar "new branch, clean root commit" 0 $? "$saida"

# 2. Branch existente, commit com segredo -> deve recusar.
printf 'SEGREDO_DE_TESTE aqui\n' > b.md
git add b.md && git commit -q -m "com segredo"
saida="$(git push -q origin HEAD:refs/heads/main 2>&1)"
avaliar "existing branch, with a secret" 1 $? "$saida"

# 3. Segredo introduzido e removido no mesmo push -> deve recusar mesmo assim.
git reset -q --hard HEAD~1
printf 'SEGREDO_DE_TESTE transitorio\n' > c.md
git add c.md && git commit -q -m "introduz"
git rm -q c.md && git commit -q -m "remove"
saida="$(git push -q origin HEAD:refs/heads/main 2>&1)"
avaliar "secret added and removed in the push" 1 $? "$saida"

# 4. Branch existente, commit limpo -> deve passar.
git reset -q --hard HEAD~2
printf 'outro procedimento\n' > d.md
git add d.md && git commit -q -m "limpo"
saida="$(git push -q origin HEAD:refs/heads/main 2>&1)"
avaliar "existing branch, clean commit" 0 $? "$saida"

# 5. Branch nova a partir de commits ja existentes -> deve passar.
saida="$(git push -q origin HEAD:refs/heads/outra 2>&1)"
avaliar "new branch, no new commits" 0 $? "$saida"

# 6. Remocao de branch -> deve passar sem varrer.
saida="$(git push -q origin --delete outra 2>&1)"
avaliar "branch deletion" 0 $? "$saida"

# 7. Primeira branch com segredo no commit raiz -> deve recusar.
git init -q --bare "$RAIZ/remoto2.git"
install -d -m 0755 "$RAIZ/remoto2.git/hooks/pre-receive.d"
cp "$RAIZ/remoto.git/hooks/pre-receive" "$RAIZ/remoto2.git/hooks/pre-receive"
cp "$HOOK" "$RAIZ/remoto2.git/hooks/pre-receive.d/gitleaks"
chmod +x "$RAIZ/remoto2.git/hooks/pre-receive" "$RAIZ/remoto2.git/hooks/pre-receive.d/gitleaks"
git clone -q "$RAIZ/remoto2.git" "$RAIZ/clone2" 2>/dev/null
cd "$RAIZ/clone2" || exit 1
git config user.email teste@lucien.local
git config user.name Teste
printf 'SEGREDO_DE_TESTE na raiz\n' > e.md
git add e.md && git commit -q -m "raiz com segredo"
saida="$(git push -q origin HEAD:refs/heads/main 2>&1)"
avaliar "root commit with a secret (no parent)" 1 $? "$saida"

printf '\n'
if [ "$FALHAS" -eq 0 ]; then
  printf 'pre-receive: all %s cases passed.\n' 7
else
  printf 'pre-receive: %s case(s) failed.\n' "$FALHAS"
  exit 1
fi
