# shellcheck shell=bash
# Valida a identidade Lucien no início de cada shell SSH interativo.
case $- in
  *i*) ;;
  *) return ;;
esac

[[ -t 0 && -t 1 ]] || return
[[ -z "${LUCIEN_AUTH_ENSURED:-}" ]] || return
export LUCIEN_AUTH_ENSURED=1
export LUCIEN_JUMP_MODE=true
export LUCIEN_ALLOW_FILE_TOKEN=true

lucien_os_user="$(id -un)"
if [[ "$lucien_os_user" == "${LUCIEN_LOCAL_ADMIN_USER:-admin}" ]]; then
  export LUCIEN_EXPECTED_USERNAME="${LUCIEN_HUB_ADMIN_USER:-Admin}"
  if ! lucien auth status >/dev/null 2>&1; then
    printf '%s\n' 'The Lucien administrative credential needs to be validated.' >&2
    lucien auth ensure >/dev/null || export LUCIEN_AUTH_FAILED=1
  fi
elif id -nG "$lucien_os_user" | tr ' ' '\n' | grep -Fqx 'lucien-primary'; then
  export LUCIEN_EXPECTED_USERNAME="$lucien_os_user"
  if ! lucien auth status >/dev/null 2>&1; then
    sudo -n /usr/local/libexec/lucien-jump-enroll || export LUCIEN_AUTH_FAILED=1
  fi
else
  export LUCIEN_AUTH_FAILED=1
fi

if [[ "${LUCIEN_AUTH_FAILED:-0}" == 1 ]] || ! lucien auth status >/dev/null 2>&1; then
  export LUCIEN_AUTH_FAILED=1
  printf '%s\n' \
    'Warning: Lucien authentication unavailable. Protected commands stay blocked.' >&2
fi

unset lucien_os_user
