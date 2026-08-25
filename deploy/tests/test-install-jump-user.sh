#!/usr/bin/env bash
set -euo pipefail

# Smoke test isolado: valida idempotência e ausência de credenciais no hook.
ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

mkdir -p "$TEST_ROOT/home" "$TEST_ROOT/bin"
cat > "$TEST_ROOT/bin/lucien" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod 0755 "$TEST_ROOT/bin/lucien"

for _ in 1 2; do
  printf 'y\n' | HOME="$TEST_ROOT/home" PATH="$TEST_ROOT/bin:$PATH" \
    bash "$ROOT_DIR/deploy/install-jump-user.sh" >/dev/null
done

hook="$TEST_ROOT/home/.config/lucien/jump-shell.sh"
source_line="[ -r '$hook' ] && . '$hook'"
test "$(stat -c '%a' "$hook")" = '600'
test "$(grep -Fxc -- "$source_line" "$TEST_ROOT/home/.bashrc")" = '1'
grep -Fq 'lucien auth ensure' "$hook"
if grep -Eqi 'luc_(tmp_)?[A-Za-z0-9_-]{8,}' "$hook" "$TEST_ROOT/home/.bashrc"; then
  printf '%s\n' 'O hook contém material semelhante a token' >&2
  exit 1
fi

printf '%s\n' 'Smoke test do hook de jump server: OK'
