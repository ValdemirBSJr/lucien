#!/usr/bin/env bash
set -euo pipefail

# Instala a integração Lucien para um jump server Debian já autenticado por SSSD.
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

erro() {
  printf 'Erro: %s\n' "$1" >&2
  exit 1
}

perguntar() {
  local mensagem="$1" padrao="$2" resposta
  read -r -p "$mensagem [$padrao]: " resposta
  printf '%s' "${resposta:-$padrao}"
}

[[ "$(uname -s)" == Linux ]] || erro 'este instalador suporta somente Linux'
(( EUID == 0 )) || erro 'execute como root'
for comando in install python3 curl openssl visudo sshd sudo runuser; do
  command -v "$comando" >/dev/null 2>&1 || erro "comando obrigatório ausente: $comando"
done
[[ -f "$SCRIPT_DIR/jump/lucien-jump-enroll.py" ]] || erro 'cópia incompleta do deploy/jump'
[[ -x /usr/local/bin/lucien ]] || erro 'instale primeiro o CLI em /usr/local/bin/lucien'
getent group lucien-primary >/dev/null || erro 'grupo lucien-primary não existe; configure o SSSD primeiro'

api_host="$(perguntar 'URL HTTPS do Hub' 'https://runbook.exemplo.interno:8443')"
[[ "$api_host" =~ ^https://[^[:space:]]+$ ]] || erro 'API_HOST deve usar HTTPS'
api_host="${api_host%/}"
ca_source="$(perguntar 'Caminho da CA pública do Hub' '/etc/lucien/ca.crt')"
[[ -r "$ca_source" ]] || erro 'CA pública não pode ser lida'
openssl x509 -in "$ca_source" -noout >/dev/null 2>&1 || erro 'arquivo informado não é um certificado X.509 válido'
local_admin="$(perguntar 'Conta local que representa o administrador' 'operador')"
hub_admin="$(perguntar 'Username administrativo correspondente no Hub' 'Admin')"
[[ "$local_admin" =~ ^[a-z_][a-z0-9_-]*$ ]] || erro 'conta local inválida'
[[ "$hub_admin" =~ ^[A-Za-z0-9_.-]{3,64}$ ]] || erro 'username administrativo inválido'

printf '%s' 'Credencial M2M luc_jump_ emitida pelo Hub (não será exibida): '
IFS= read -r -s enrollment_token
printf '\n'
[[ "$enrollment_token" == luc_jump_* ]] || erro 'credencial M2M inválida'
(( ${#enrollment_token} <= 4096 )) || erro 'credencial M2M excede o limite'

# Falhe antes de modificar o host se URL, CA ou certificado não forem coerentes.
curl --fail --silent --show-error \
  --cacert "$ca_source" \
  "$api_host/health" >/dev/null || erro 'não foi possível validar o Hub por TLS'
grep -Eq '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config.d/\*\.conf' \
  /etc/ssh/sshd_config || erro 'sshd_config não inclui /etc/ssh/sshd_config.d/*.conf'
sshd -t || erro 'a configuração SSH atual já é inválida'

secret_tmp="$(mktemp)"
config_tmp="$(mktemp)"
profile_tmp="$(mktemp)"
sudoers_tmp="$(mktemp)"
trap 'rm -f "$secret_tmp" "$config_tmp" "$profile_tmp" "$sudoers_tmp"' EXIT
printf '%s\n' "$enrollment_token" >"$secret_tmp"

install -d -o root -g root -m 0755 /etc/lucien
install -d -o root -g root -m 0700 /etc/lucien/secrets
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0644 "$ca_source" /etc/lucien/jump-ca.crt
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/jump/lucien-jump-enroll.py" \
  /usr/local/libexec/lucien-jump-enroll
install -o root -g root -m 0600 "$secret_tmp" \
  /etc/lucien/secrets/jump_enrollment_key
rm -f "$secret_tmp"
unset enrollment_token

cat >"$config_tmp" <<EOF
API_HOST=$api_host
TLS_CA_FILE=/etc/lucien/jump-ca.crt
LUCIEN_BINARY=/usr/local/bin/lucien
EOF
install -o root -g root -m 0644 "$config_tmp" /etc/lucien/jump.conf

printf 'export API_HOST=%q\n' "$api_host" >"$profile_tmp"
printf 'export TLS_CA_FILE=%q\n' '/etc/lucien/jump-ca.crt' >>"$profile_tmp"
printf 'export LUCIEN_LOCAL_ADMIN_USER=%q\n' "$local_admin" >>"$profile_tmp"
printf 'export LUCIEN_HUB_ADMIN_USER=%q\n' "$hub_admin" >>"$profile_tmp"
install -o root -g root -m 0644 "$profile_tmp" \
  /etc/profile.d/29-lucien-jump-config.sh
install -o root -g root -m 0644 "$SCRIPT_DIR/jump/jump-shell.sh" \
  /etc/profile.d/30-lucien-jump-auth.sh

cat >"$sudoers_tmp" <<'EOF'
%lucien-primary ALL=(root) NOPASSWD: /usr/local/libexec/lucien-jump-enroll
EOF
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null || erro 'regra sudoers inválida'
install -o root -g root -m 0440 "$sudoers_tmp" \
  /etc/sudoers.d/lucien-jump-enroll

install -o root -g root -m 0644 "$SCRIPT_DIR/jump/issue.net" /etc/issue.net
install -o root -g root -m 0644 "$SCRIPT_DIR/jump/motd" /etc/motd
install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
printf '%s\n' 'Banner /etc/issue.net' > /etc/ssh/sshd_config.d/01-lucien-banner.conf
chmod 0644 /etc/ssh/sshd_config.d/01-lucien-banner.conf
if ! sshd -t; then
  rm -f /etc/ssh/sshd_config.d/01-lucien-banner.conf
  erro 'configuração SSH inválida; o drop-in do banner foi removido'
fi

if systemctl is-active --quiet ssh; then
  systemctl reload ssh
elif systemctl is-active --quiet sshd; then
  systemctl reload sshd
fi

printf '%s\n' 'Integração do jump server instalada com sucesso.'
printf '%s\n' 'Abra uma nova sessão SSH LDAP para validar o provisionamento automático.'
