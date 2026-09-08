#!/usr/bin/env bash
set -euo pipefail

# Instala o Lucien CLI nativo em Linux e configura somente dados públicos do Hub.
# Tokens e a chave de bootstrap nunca são gravados por este script.
umask 077

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
declare -a PRIVILEGE_COMMAND=()

erro() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

confirmar() {
  local resposta
  read -r -p "$1 [y/N]: " resposta
  [[ "$resposta" =~ ^([yY]|[yY][eE][sS])$ ]]
}

perguntar() {
  local rotulo="$1"
  local padrao="$2"
  local valor

  read -r -p "$rotulo [$padrao]: " valor
  printf '%s' "${valor:-$padrao}"
}

exigir_comando() {
  command -v "$1" >/dev/null 2>&1 || erro "$1 was not found"
}

preparar_privilegios() {
  if (( EUID == 0 )); then
    PRIVILEGE_COMMAND=()
    return
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo -v || erro 'could not obtain privileges with sudo'
    PRIVILEGE_COMMAND=(sudo)
    return
  fi

  erro 'sudo is not available; run the system-wide install as root'
}

executar_privilegiado() {
  if (( ${#PRIVILEGE_COMMAND[@]} > 0 )); then
    "${PRIVILEGE_COMMAND[@]}" "$@"
  else
    "$@"
  fi
}

locale_utf8_alvo() {
  # Preserva idioma e região; troca somente a codificação. Um host pt_BR vira
  # pt_BR.UTF-8, nunca en_US.UTF-8 — trocar o idioma seria efeito colateral.
  local atual="${LANG:-}"
  local base="${atual%%.*}"

  case "$base" in
    ''|C|POSIX) printf 'C.UTF-8' ;;
    *) printf '%s.UTF-8' "$base" ;;
  esac
}

locale_disponivel() {
  # `locale -a` imprime pt_BR.utf8; o nome canônico é pt_BR.UTF-8. Compara sem
  # diferenciar maiúsculas nem o hífen para evitar um falso negativo.
  # Expansão do próprio bash: `locale -a` lista centenas de entradas e um par de
  # subprocessos por linha tornaria a checagem lenta sem necessidade.
  local alvo="${1,,}"
  local candidato
  alvo="${alvo//-/}"
  while read -r candidato; do
    candidato="${candidato,,}"
    [[ "${candidato//-/}" != "$alvo" ]] || return 0
  done < <(locale -a 2>/dev/null)
  return 1
}

configurar_locale_utf8() {
  # A captura, o Markdown e a descrição de `lucien start -d` são UTF-8. Num
  # locale de byte único o operador não consegue digitar acentos e o runbook
  # fica ilegível no editor. Só o LANG é tocado: LC_* e teclado não mudam.
  local alvo

  if [[ "${LC_ALL:-}" != '' ]]; then
    printf 'Warning: LC_ALL=%s takes precedence over LANG and will not be changed.\n' \
      "$LC_ALL" >&2
    printf '  Set it manually to a .UTF-8 variant.\n' >&2
    return
  fi

  if [[ "${LANG:-}" == *.[uU][tT][fF]8 || "${LANG:-}" == *.[uU][tT][fF]-8 ]]; then
    printf '  locale:       %s (UTF-8, kept)\n' "$LANG"
    return
  fi

  alvo="$(locale_utf8_alvo)"
  printf '\nThe current locale (%s) is not UTF-8.\n' "${LANG:-not set}"
  printf 'Without UTF-8, accented input to -d is rejected and the runbook comes out unreadable.\n'

  if ! locale_disponivel "$alvo"; then
    printf 'Locale %s is not generated on this host. Generate it and try again:\n' "$alvo" >&2
    printf '  sudo locale-gen %s && sudo update-locale LANG=%s\n' "$alvo" "$alvo" >&2
    return
  fi

  if ! command -v localectl >/dev/null 2>&1; then
    printf 'localectl is not available. Set it manually:\n' >&2
    printf '  sudo update-locale LANG=%s\n' "$alvo" >&2
    return
  fi

  confirmar "Set LANG=$alvo for the whole host (language and keyboard preserved)" || {
    printf 'Locale kept. To set it later: sudo localectl set-locale LANG=%s\n' \
      "$alvo"
    return
  }

  executar_privilegiado localectl set-locale "LANG=$alvo" || {
    printf 'Warning: could not set the locale; set it manually.\n' >&2
    return
  }
  printf '  locale:       %s (applied; reopen the session for it to take effect)\n' "$alvo"
}

adicionar_linha_usuario() {
  local arquivo="$1"
  local linha="$2"

  [[ -e "$arquivo" ]] || install -m 0600 /dev/null "$arquivo"
  if ! grep -Fqx -- "$linha" "$arquivo" 2>/dev/null; then
    printf '\n%s\n' "$linha" >> "$arquivo"
  fi
}

gerar_completion() {
  local shell="$1"
  local destino="$2"
  local privilegiado="$3"
  local gerado="$temporario/completion-$shell"

  "$bin_origem" completion "$shell" > "$gerado"
  [[ -s "$gerado" ]] || erro "the binary produced no completion for $shell"

  if [[ "$privilegiado" == 'true' ]]; then
    executar_privilegiado install -d -o root -g root -m 0755 "$(dirname -- "$destino")"
    executar_privilegiado install -o root -g root -m 0644 "$gerado" "$destino"
  else
    install -d -m 0755 "$(dirname -- "$destino")"
    install -m 0644 "$gerado" "$destino"
  fi
  printf '  completion:   %s\n' "$destino"
}

instalar_completion_usuario() {
  local shell_login="${SHELL:-}"
  local destino arquivo_inicio linha

  shell_login="${shell_login##*/}"

  case "$shell_login" in
    bash)
      destino="$env_dir/completion.bash"
      gerar_completion bash "$destino" false
      arquivo_inicio="$HOME/.bashrc"
      linha="[ -r '$destino' ] && . '$destino'"
      adicionar_linha_usuario "$arquivo_inicio" "$linha"
      ;;
    zsh)
      destino="$env_dir/completion.zsh"
      gerar_completion zsh "$destino" false
      arquivo_inicio="$HOME/.zshrc"
      adicionar_linha_usuario "$arquivo_inicio" \
        'autoload -Uz compinit && (( $+functions[compdef] )) || compinit'
      linha="[ -r '$destino' ] && . '$destino'"
      adicionar_linha_usuario "$arquivo_inicio" "$linha"
      ;;
    fish)
      destino="$HOME/.config/fish/completions/lucien.fish"
      gerar_completion fish "$destino" false
      ;;
    *)
      printf '  completion:   not installed; unsupported login shell: %s\n' \
        "${shell_login:-unknown}"
      ;;
  esac
}

instalar_completion_sistema() {
  gerar_completion bash \
    '/usr/local/share/bash-completion/completions/lucien' true
  gerar_completion zsh \
    '/usr/local/share/zsh/site-functions/_lucien' true
  gerar_completion fish \
    '/usr/local/share/fish/vendor_completions.d/lucien.fish' true
}

validar_valor_shell() {
  local nome="$1"
  local valor="$2"

  [[ -n "$valor" ]] || erro "$nome cannot be empty"
  [[ "$valor" != *$'\n'* && "$valor" != *$'\r'* && "$valor" != *"'"* ]] || \
    erro "$nome contains characters incompatible with the environment file"
}

ler_api_host_padrao() {
  local chave valor

  [[ -r "$PROJECT_ROOT/.env" ]] || return 0
  while IFS='=' read -r chave valor; do
    if [[ "$chave" == 'API_HOST' && -n "$valor" ]]; then
      printf '%s' "$valor"
      return 0
    fi
  done < "$PROJECT_ROOT/.env"
  return 0
}

detectar_arquivo_pacote() {
  local arquitetura="$1"
  local -a pacotes=()

  shopt -s nullglob
  pacotes=("$PROJECT_ROOT"/dist/lucien_*_linux_"$arquitetura".tar.gz)
  shopt -u nullglob
  if (( ${#pacotes[@]} == 1 )); then
    printf '%s' "${pacotes[0]}"
  fi
  return 0
}

uso() {
  cat <<'EOF'
Usage:
  ./deploy/install-cli.sh
      Interactively installs and configures the native Lucien CLI on Linux.

  ./deploy/install-cli.sh --help
      Shows this help.
EOF
}

case "${1:-}" in
  '') ;;
  --help|-h)
    uso
    exit 0
    ;;
  *)
    uso >&2
    erro "unknown option: $1"
    ;;
esac

[[ "$(uname -s)" == 'Linux' ]] || erro 'this installer supports Linux only'
case "$(uname -m)" in
  x86_64) arquitetura='amd64' ;;
  aarch64|arm64) arquitetura='arm64' ;;
  *) erro "unsupported architecture: $(uname -m)" ;;
esac

exigir_comando install
exigir_comando mktemp
exigir_comando openssl
exigir_comando sha256sum
exigir_comando tar

printf '%s\n' 'Guided installation of the Lucien CLI for Linux'
printf '%s\n' 'The bootstrap key is held in memory only, and only if you choose to create the first admin.'
printf '%s\n' 'Provide the public CA generated on the Hub; this client never creates a certificate authority.'
printf '%s\n' 'Scope: 1) current user (~/.local/bin)  2) system-wide (/usr/local/bin)'
read -r -p 'Choose [1]: ' escopo
escopo="${escopo:-1}"

arquivo_pacote_padrao="$(detectar_arquivo_pacote "$arquitetura")"
if [[ -z "$arquivo_pacote_padrao" ]]; then
  arquivo_pacote_padrao="/path/lucien_VERSION_linux_${arquitetura}.tar.gz"
fi
arquivo_pacote="$(perguntar 'Lucien CLI package, alongside its checksum' "$arquivo_pacote_padrao")"
[[ -f "$arquivo_pacote" ]] || erro "package not found: $arquivo_pacote"
[[ -f "$arquivo_pacote.sha256" ]] || erro "checksum not found: $arquivo_pacote.sha256"

nome_pacote="$(basename -- "$arquivo_pacote")"
[[ "$nome_pacote" =~ ^(lucien_[A-Za-z0-9._-]+_linux_(amd64|arm64))\.tar\.gz$ ]] || \
  erro 'the package does not follow the lucien_VERSION_linux_ARCH.tar.gz pattern'
diretorio_pacote="${BASH_REMATCH[1]}"
[[ "${BASH_REMATCH[2]}" == "$arquitetura" ]] || \
  erro "the package does not match architecture $arquitetura"

(
  cd -- "$(dirname -- "$arquivo_pacote")"
  sha256sum -c -- "$(basename -- "$arquivo_pacote.sha256")"
)

ca_origem_padrao="$PROJECT_ROOT/certs/ca.crt"
ca_origem="$(perguntar 'Path to the Hub public CA' "$ca_origem_padrao")"
[[ -f "$ca_origem" ]] || \
  erro "CA not found: $ca_origem. Generate the certificates on the Hub and copy only certs/ca.crt to this host"
openssl x509 -in "$ca_origem" -noout >/dev/null 2>&1 || erro 'the CA holds no valid PEM certificate'
texto_ca="$(openssl x509 -in "$ca_origem" -noout -text)"
[[ "$texto_ca" == *'CA:TRUE'* ]] || erro 'the certificate provided is not a CA'
[[ "$texto_ca" == *'Certificate Sign'* ]] || \
  erro 'the CA has no keyUsage for certificate signing'

api_host_padrao="$(ler_api_host_padrao)"
api_host_padrao="${api_host_padrao:-https://localhost:8443}"
api_host="$(perguntar 'HTTPS URL the CLI uses to reach the Hub' "$api_host_padrao")"
validar_valor_shell 'API_HOST' "$api_host"
[[ "$api_host" == https://* ]] || erro 'API_HOST must start with https://'
[[ "$api_host" != *'?'* && "$api_host" != *'#'* ]] || \
  erro 'API_HOST must not carry a query string or fragment'
autoridade="${api_host#https://}"
autoridade="${autoridade%%/*}"
[[ -n "$autoridade" && "$autoridade" != *'@'* ]] || \
  erro 'API_HOST must name a host and must not include credentials'
api_host="${api_host%/}"

editor_padrao="${EDITOR:-vi}"
editor="$(perguntar 'Editor used to write the runbooks' "$editor_padrao")"
validar_valor_shell 'EDITOR' "$editor"

case "$escopo" in
  1)
    [[ -n "${HOME:-}" ]] || erro 'HOME is not set, and the per-user install needs it'
    usuario_home="$HOME"
    bin_dir="$usuario_home/.local/bin"
    ca_dir="$usuario_home/.local/share/lucien"
    env_dir="$usuario_home/.config/lucien"
    env_file="$env_dir/env"
    profile_file="$usuario_home/.profile"
    install -d -m 0755 "$bin_dir" "$ca_dir"
    install -d -m 0700 "$env_dir"
    ;;
  2)
    preparar_privilegios
    bin_dir='/usr/local/bin'
    ca_dir='/etc/lucien'
    env_dir='/etc/profile.d'
    env_file='/etc/profile.d/lucien.sh'
    executar_privilegiado install -d -o root -g root -m 0755 "$bin_dir" "$ca_dir" "$env_dir"
    ;;
  *) erro 'invalid scope' ;;
esac

temporario="$(mktemp -d)"
trap 'rm -rf -- "$temporario"' EXIT
tar -xzf "$arquivo_pacote" -C "$temporario" "$diretorio_pacote/lucien"
bin_origem="$temporario/$diretorio_pacote/lucien"
[[ -f "$bin_origem" && ! -L "$bin_origem" ]] || erro 'the package holds no regular Lucien binary'
"$bin_origem" help >/dev/null

bin_destino="$bin_dir/lucien"
ca_destino="$ca_dir/ca.crt"
env_temporario="$temporario/lucien-env"
{
  printf '%s\n' '# Gerado por deploy/install-cli.sh. Não contém tokens.'
  if [[ "$escopo" == '1' ]]; then
    printf "export PATH='%s':\"\$PATH\"\n" "$bin_dir"
  fi
  printf "export API_HOST='%s'\n" "$api_host"
  printf "export TLS_CA_FILE='%s'\n" "$ca_destino"
  printf "export EDITOR='%s'\n" "$editor"
} > "$env_temporario"

if [[ "$escopo" == '1' ]]; then
  install -m 0755 "$bin_origem" "$bin_destino"
  install -m 0644 "$ca_origem" "$ca_destino"
  install -m 0600 "$env_temporario" "$env_file"
  [[ -e "$profile_file" ]] || install -m 0600 /dev/null "$profile_file"
  source_line="[ -f '$env_file' ] && . '$env_file'"
  if ! grep -Fqx -- "$source_line" "$profile_file" 2>/dev/null; then
    printf '\n%s\n' "$source_line" >> "$profile_file"
  fi
else
  executar_privilegiado install -o root -g root -m 0755 "$bin_origem" "$bin_destino"
  executar_privilegiado install -o root -g root -m 0644 "$ca_origem" "$ca_destino"
  executar_privilegiado install -o root -g root -m 0644 "$env_temporario" "$env_file"
fi

printf '\nInstalled files:\n'
printf '  binary:       %s\n' "$bin_destino"
printf '  public CA:    %s\n' "$ca_destino"
printf '  environment:  %s\n' "$env_file"
if [[ "$escopo" == '1' ]]; then
  instalar_completion_usuario
else
  instalar_completion_sistema
fi
if [[ "$escopo" == '1' ]]; then
  printf '  profile:      %s\n' "$profile_file"
fi
configurar_locale_utf8

if confirmar 'Validate the TLS connection to the /health endpoint now'; then
  exigir_comando curl
  curl --fail --silent --show-error --cacert "$ca_destino" "$api_host/health"
  printf '\nTLS connection validated.\n'
fi

if confirmar 'Create the first administrator user now'; then
  administrador="$(perguntar 'Name of the first administrator' 'administrator')"
  [[ "$administrador" =~ ^[A-Za-z0-9_.-]{3,64}$ ]] || erro 'invalid username'
  read -r -s -p 'LUCIEN_BOOTSTRAP_KEY (never shown, never saved): ' bootstrap_key
  printf '\n'
  [[ -n "$bootstrap_key" ]] || erro 'the bootstrap key cannot be empty'

  permitir_fallback='false'
  printf '%s\n' 'On a server with no graphical keyring, the CLI can keep the token in a chmod 600 file.'
  if confirmar 'Allow that fallback, only to save this administrator token'; then
    permitir_fallback='true'
  fi

  API_HOST="$api_host" \
  TLS_CA_FILE="$ca_destino" \
  EDITOR="$editor" \
  LUCIEN_BOOTSTRAP_KEY="$bootstrap_key" \
  LUCIEN_ALLOW_FILE_TOKEN="$permitir_fallback" \
    "$bin_destino" create user "$administrador"
  unset bootstrap_key

  printf '%s\n' 'Profile and credential were saved by the CLI itself, for the user who ran this script.'
  printf '%s\n' 'Now set USER_CREATION_ENABLED=false in the Hub .env and recreate the hub service only.'
fi

printf '\nTo use the command in this shell, run:\n  . %q\n' "$env_file"
printf '%s\n' 'New login shells load the configuration automatically.'
