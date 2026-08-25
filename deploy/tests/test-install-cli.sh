#!/usr/bin/env bash
set -euo pipefail

# Smoke test isolado: não toca no HOME real nem requer um Hub em execução.
ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

case "$(uname -m)" in
  x86_64) arquitetura='amd64' ;;
  aarch64|arm64) arquitetura='arm64' ;;
  *) printf 'Architecture not supported by this test\n' >&2; exit 1 ;;
esac

pacote_dir="lucien_test_linux_${arquitetura}"
mkdir -p "$TEST_ROOT/pacote/$pacote_dir" "$TEST_ROOT/home"
cat > "$TEST_ROOT/pacote/$pacote_dir/lucien" <<'EOF'
#!/bin/sh
if [ "${1:-}" = 'completion' ] && [ -n "${2:-}" ]; then
  printf '# completion %s\n' "$2"
fi
exit 0
EOF
chmod 0755 "$TEST_ROOT/pacote/$pacote_dir/lucien"
tar -C "$TEST_ROOT/pacote" -czf "$TEST_ROOT/$pacote_dir.tar.gz" "$pacote_dir"
(
  cd "$TEST_ROOT"
  sha256sum "$pacote_dir.tar.gz" > "$pacote_dir.tar.gz.sha256"
)

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out "$TEST_ROOT/ca.key" >/dev/null 2>&1
openssl req -x509 -new -sha256 -days 1 \
  -key "$TEST_ROOT/ca.key" \
  -subj '/C=BR/O=Lucien Test/CN=Lucien Test CA' \
  -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -out "$TEST_ROOT/ca.crt"

printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  HOME="$TEST_ROOT/home" SHELL=/bin/bash EDITOR=vi \
    bash "$ROOT_DIR/deploy/install-cli.sh"

test -x "$TEST_ROOT/home/.local/bin/lucien"
test -r "$TEST_ROOT/home/.local/share/lucien/ca.crt"
test -f "$TEST_ROOT/home/.config/lucien/env"
test "$(stat -c '%a' "$TEST_ROOT/home/.config/lucien/env")" = '600'
grep -Fq "export API_HOST='https://hub.test:8443'" "$TEST_ROOT/home/.config/lucien/env"
grep -Fq 'TLS_CA_FILE=' "$TEST_ROOT/home/.config/lucien/env"
grep -Fq "$TEST_ROOT/home/.config/lucien/env" "$TEST_ROOT/home/.profile"
test -s "$TEST_ROOT/home/.config/lucien/completion.bash"
completion_source="[ -r '$TEST_ROOT/home/.config/lucien/completion.bash' ] && . '$TEST_ROOT/home/.config/lucien/completion.bash'"
grep -Fqx -- "$completion_source" "$TEST_ROOT/home/.bashrc"
if grep -Eq 'BOOTSTRAP|TOKEN' "$TEST_ROOT/home/.config/lucien/env"; then
  printf 'The persisted file contains a sensitive variable name\n' >&2
  exit 1
fi

# Reinstalação deve atualizar os artefatos sem duplicar linhas no shell.
printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  HOME="$TEST_ROOT/home" SHELL=/bin/bash EDITOR=vi \
    bash "$ROOT_DIR/deploy/install-cli.sh" >/dev/null
test "$(grep -Fxc -- "$completion_source" "$TEST_ROOT/home/.bashrc")" = '1'

mkdir -p "$TEST_ROOT/home-zsh" "$TEST_ROOT/home-fish"
printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  HOME="$TEST_ROOT/home-zsh" SHELL=/bin/zsh EDITOR=vi \
    bash "$ROOT_DIR/deploy/install-cli.sh" >/dev/null
test -s "$TEST_ROOT/home-zsh/.config/lucien/completion.zsh"
grep -Fq 'completion.zsh' "$TEST_ROOT/home-zsh/.zshrc"
grep -Fq 'compinit' "$TEST_ROOT/home-zsh/.zshrc"

printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  HOME="$TEST_ROOT/home-fish" SHELL=/usr/bin/fish EDITOR=vi \
    bash "$ROOT_DIR/deploy/install-cli.sh" >/dev/null
test -s "$TEST_ROOT/home-fish/.config/fish/completions/lucien.fish"

printf '%s\n' 'CLI installer smoke test: OK'

# Locale: a captura e o Markdown são UTF-8. O instalador deve detectar o caso,
# nunca trocar idioma ou teclado, e jamais travar esperando entrada extra.
mkdir -p "$TEST_ROOT/home-locale-ok" "$TEST_ROOT/home-locale-lcall"

saida_locale_ok="$TEST_ROOT/locale-ok.txt"
printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  HOME="$TEST_ROOT/home-locale-ok" SHELL=/bin/bash EDITOR=vi \
    LANG=pt_BR.UTF-8 LC_ALL='' \
    bash "$ROOT_DIR/deploy/install-cli.sh" > "$saida_locale_ok" 2>&1
grep -Fq 'UTF-8, kept' "$saida_locale_ok" || {
  printf 'an existing UTF-8 locale should be preserved untouched\n' >&2
  exit 1
}
if grep -Fq 'set-locale' "$saida_locale_ok"; then
  printf 'installer tried to change a locale that was already UTF-8\n' >&2
  exit 1
fi

# LC_ALL tem precedência sobre LANG; alterar apenas LANG não teria efeito, então
# o instalador avisa em vez de aplicar uma mudança inócua.
saida_locale_lcall="$TEST_ROOT/locale-lcall.txt"
printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  HOME="$TEST_ROOT/home-locale-lcall" SHELL=/bin/bash EDITOR=vi \
    LANG=C LC_ALL=C \
    bash "$ROOT_DIR/deploy/install-cli.sh" > "$saida_locale_lcall" 2>&1
grep -Fq 'LC_ALL' "$saida_locale_lcall" || {
  printf 'LC_ALL precedence was not reported\n' >&2
  exit 1
}
test -x "$TEST_ROOT/home-locale-lcall/.local/bin/lucien"

# Imagens Linux mínimas podem não declarar LANG. Com `set -u`, isso não pode
# interromper a instalação; o instalador deve tratar o caso como locale vazio.
mkdir -p "$TEST_ROOT/home-locale-unset"
saida_locale_unset="$TEST_ROOT/locale-unset.txt"
printf '1\n%s\n%s\nhttps://hub.test:8443\nvi\nn\nn\n' \
  "$TEST_ROOT/$pacote_dir.tar.gz" "$TEST_ROOT/ca.crt" | \
  env -u LANG -u LC_ALL \
    HOME="$TEST_ROOT/home-locale-unset" SHELL=/bin/bash EDITOR=vi \
    bash "$ROOT_DIR/deploy/install-cli.sh" > "$saida_locale_unset" 2>&1
test -x "$TEST_ROOT/home-locale-unset/.local/bin/lucien"
grep -Fq 'not set' "$saida_locale_unset" || {
  printf 'an unset LANG was not handled explicitly\n' >&2
  exit 1
}
