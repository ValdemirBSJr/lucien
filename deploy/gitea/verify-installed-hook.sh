#!/usr/bin/env bash
# Prova o hook JA INSTALADO no servidor Gitea, sem tocar no repositorio real.
#
# A copia que importa nao e a do repositorio de codigo: e a que esta em
# hooks/pre-receive.d/ do repositorio bare. E ela que o Gitea executa, e e ela
# que este script exercita, num repositorio descartavel em /tmp.
#
# Nao fala com o Gitea: sem clone, sem push, sem credencial. So sistema de
# arquivos.
#
# Uso:
#   bash verify-installed-hook.sh [caminho-do-repositorio-bare]
#
# Padrao:
#   /var/lib/gitea/data/gitea-repositories/admin/runbooks.git
set -u

REPO_REAL="${1:-/var/lib/gitea/data/gitea-repositories/admin/runbooks.git}"

# O hook roda como o dono do repositorio, nao como root. Root le qualquer
# coisa, entao um teste como root aprovaria mesmo com o gitleaks ou a
# configuracao inacessiveis para esse usuario -- e a producao recusaria todo
# push por falha fechada, que e o oposto do que o teste teria dito.
if (( EUID == 0 )) && [[ "${LUCIEN_HOOK_CHECK_REEXEC:-}" != '1' ]]; then
  if [[ -d "$REPO_REAL" ]]; then
    DONO="$(stat -c '%U' "$REPO_REAL" 2>/dev/null || true)"
  else
    DONO=''
  fi
  if [[ -n "$DONO" && "$DONO" != 'root' ]] && command -v runuser >/dev/null 2>&1
  then
    printf 'Running as root; re-running as %s, the repository owner.\n\n' "$DONO"
    export LUCIEN_HOOK_CHECK_REEXEC=1
    exec runuser -u "$DONO" -- bash "$0" "$@"
  fi
  printf 'Warning: running as root. root reads anything, so a pass here does\n' >&2
  printf '         not prove the hook works for the user Gitea runs it as.\n' >&2
  printf '         Prefer: runuser -u %s -- bash %s\n\n' "${DONO:-git}" "$0" >&2
fi
GITLEAKS_BIN="${LUCIEN_GITLEAKS_BIN:-/usr/local/bin/gitleaks}"
GITLEAKS_CONFIG="${LUCIEN_GITLEAKS_CONFIG:-/etc/lucien/gitleaks.toml}"
SEGREDO='snmp-server community S3cr3tRW RW' # gitleaks:allow

FALHAS=0
TRABALHO="$(mktemp -d /tmp/lucien-hook-check.XXXXXX)"

limpar() {
  case "$TRABALHO" in
    /tmp/lucien-hook-check.*) rm -rf -- "$TRABALHO" ;;
    *) printf 'Unexpected directory; refusing to clean: %s\n' "$TRABALHO" >&2 ;;
  esac
}
trap limpar EXIT

erro()  { printf 'Error: %s\n' "$1" >&2; exit 1; }
ok()    { printf '  OK      %s\n' "$1"; }
falha() { printf '  FAILED  %s\n' "$1"; FALHAS=$((FALHAS + 1)); }

# --- 1. o que sera exercitado -----------------------------------------------

printf '== what is installed\n'

# `-d` responde falso tanto para caminho inexistente quanto para diretorio-pai
# que este usuario nao pode atravessar -- e o segundo e o caso comum, porque a
# arvore do Gitea costuma ser 0750 do usuario de servico. Dizer "not found" ali
# manda o operador procurar um erro de digitacao que nao existe.
if [[ ! -d "$REPO_REAL" ]]; then
  if (( EUID != 0 )); then
    printf 'Error: cannot reach %s as %s.\n' "$REPO_REAL" "$(id -un)" >&2
    printf '       It may exist and simply not be traversable by this user:\n' >&2
    printf '       the Gitea tree is usually restricted to the service account.\n' >&2
    printf '       Run this as root -- it drops to the repository owner on its own.\n' >&2
    exit 1
  fi
  erro "bare repository not found: $REPO_REAL"
fi

HOOK_INSTALADO="$REPO_REAL/hooks/pre-receive.d/gitleaks"
WRAPPER="$REPO_REAL/hooks/pre-receive"
[[ -x "$HOOK_INSTALADO" ]] || erro "hook missing or not executable: $HOOK_INSTALADO"
[[ -x "$WRAPPER" ]] || erro "wrapper missing: $WRAPPER"

printf '  hook:     %s (%s bytes)\n' "$HOOK_INSTALADO" \
  "$(wc -c < "$HOOK_INSTALADO")"
printf '  gitleaks: %s\n' "$GITLEAKS_BIN"
printf '  config:   %s\n' "$GITLEAKS_CONFIG"

# O hook falha fechado quando nao alcanca o binario ou a configuracao. Sem esta
# checagem, os tres casos abaixo recusariam por motivo errado e o resultado
# pareceria correto.
[[ -x "$GITLEAKS_BIN" ]] || \
  erro "gitleaks is not executable by this user: $GITLEAKS_BIN"
[[ -r "$GITLEAKS_CONFIG" ]] || \
  erro "configuration unreadable by this user: $GITLEAKS_CONFIG (try sudo -u git)"

# --- 2. repositorio descartavel com a copia instalada -----------------------

BARE="$TRABALHO/hooktest.git"
git init -q --bare "$BARE" || erro 'could not create the bare repository'
install -d -m 0755 "$BARE/hooks/pre-receive.d"
# Somente o gitleaks: o hook `gitea` do mesmo diretorio exige ambiente do
# servidor e nao tem o que fazer num repositorio solto.
cp "$WRAPPER" "$BARE/hooks/pre-receive"
cp "$HOOK_INSTALADO" "$BARE/hooks/pre-receive.d/gitleaks"
chmod +x "$BARE/hooks/pre-receive" "$BARE/hooks/pre-receive.d/gitleaks"

CLONE="$TRABALHO/clone"
git clone -q "$BARE" "$CLONE" 2>/dev/null
cd "$CLONE" || erro 'could not enter the clone'
git config user.email hook-check@lucien.local
git config user.name 'Hook check'

empurrar() {
  git push -q origin HEAD:refs/heads/main 2>&1
}

printf '\n== cases\n'

# --- 3. limpo passa ---------------------------------------------------------

printf 'Clean procedure, no secret.\n' > check.md
git add check.md && git commit -q -m 'clean'
saida="$(empurrar)"; rc=$?
if [[ "$rc" -eq 0 ]]; then
  ok 'clean commit is accepted'
else
  falha "clean commit was rejected (rc=$rc)"
  printf '%s\n' "$saida" | sed 's/^/          /'
fi

# --- 4. segredo e recusado --------------------------------------------------

printf '%s\n' "$SEGREDO" > check.md
git add check.md && git commit -q -m 'with a secret'
saida_segredo="$(empurrar)"; rc=$?
if [[ "$rc" -ne 0 ]]; then
  ok 'commit carrying a secret is rejected'
else
  falha 'commit carrying a secret PASSED -- the hook is not blocking'
fi

# O valor nao pode aparecer na recusa: o hook usa --redact=100.
if printf %s "$saida_segredo" | grep -q 'S3cr3tRW'; then
  falha 'the secret value leaked into the rejection message'
else
  ok 'the secret value does not appear in the rejection'
fi

# --- 5. segredo introduzido e removido no mesmo push ------------------------

git reset -q --hard HEAD~1
printf '%s\n' "$SEGREDO" > transient.md
git add transient.md && git commit -q -m 'introduce'
git rm -q transient.md && git commit -q -m 'remove'
saida="$(empurrar)"; rc=$?
if [[ "$rc" -ne 0 ]]; then
  ok 'secret added and removed in the same push is rejected'
else
  falha 'transient secret PASSED -- the hook reads the net diff, not the history'
fi
git reset -q --hard HEAD~2

# --- 6. a mensagem e a versao atual -----------------------------------------

printf '\n== rejection message\n'

if printf %s "$saida_segredo" | grep -q 'PUSH REJECTED'; then
  ok 'English text (PUSH REJECTED)'
elif printf %s "$saida_segredo" | grep -q 'PUSH RECUSADO'; then
  falha 'STALE copy installed: it still prints PUSH RECUSADO'
else
  falha 'rejected without a recognizable message'
fi

if printf %s "$saida_segredo" | grep -q 'SUA_SENHA_AQUI'; then
  ok 'names the placeholder the DLP actually writes (SUA_SENHA_AQUI)'
elif printf %s "$saida_segredo" | grep -q 'YOUR_PASSWORD_HERE'; then
  falha 'copy carries the fixed defect: names YOUR_PASSWORD_HERE, which Lucien never produces'
else
  falha 'the message gives no placeholder guidance'
fi

printf '\n--- rejection, as the operator sees it ---\n'
printf '%s\n' "$saida_segredo" | sed 's/^/  /'
printf -- '------------------------------------------\n'

# --- veredito ---------------------------------------------------------------

printf '\n'
if [[ "$FALHAS" -gt 0 ]]; then
  printf '%d check(s) failed. The installed hook is NOT correct.\n' "$FALHAS"
  exit 1
fi
printf 'Installed hook validated: 6 checks, no failures.\n'
printf 'Nothing was written to %s\n' "$REPO_REAL"
