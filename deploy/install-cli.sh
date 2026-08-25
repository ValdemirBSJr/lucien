#!/usr/bin/env bash
set -euo pipefail

# Instala o Lucien CLI nativo em Linux e configura somente dados públicos do Hub.
# Tokens e a chave de bootstrap nunca são gravados por este script.
umask 077

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
declare -a PRIVILEGE_COMMAND=()

erro() {
  printf 'Erro: %s\n' "$1" >&2
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
  command -v "$1" >/dev/null 2>&1 || erro "$1 não foi encontrado"
}

preparar_privilegios() {
  if (( EUID == 0 )); then
    PRIVILEGE_COMMAND=()
    return
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo -v || erro 'não foi possível obter privilégios com sudo'
    PRIVILEGE_COMMAND=(sudo)
    return
  fi

  erro 'sudo não está disponível; execute a instalação de sistema como root'
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
  local base="${LANG%%.*}"

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
    printf 'Aviso: LC_ALL=%s tem precedência sobre LANG e não será alterado.\n' \
      "$LC_ALL" >&2
    printf '  Ajuste-o manualmente para uma variante .UTF-8.\n' >&2
    return
  fi

  if [[ "${LANG:-}" == *.[uU][tT][fF]8 || "${LANG:-}" == *.[uU][tT][fF]-8 ]]; then
    printf '  locale:       %s (UTF-8, mantido)\n' "$LANG"
    return
  fi

  alvo="$(locale_utf8_alvo)"
  printf '\nO locale atual (%s) não é UTF-8.\n' "${LANG:-nao definido}"
  printf 'Sem UTF-8, acentos em -d são rejeitados e o runbook sai ilegível.\n'

  if ! locale_disponivel "$alvo"; then
    printf 'O locale %s não está gerado neste host. Gere-o e repita:\n' "$alvo" >&2
    printf '  sudo locale-gen %s && sudo update-locale LANG=%s\n' "$alvo" "$alvo" >&2
    return
  fi

  if ! command -v localectl >/dev/null 2>&1; then
    printf 'localectl não está disponível. Ajuste manualmente:\n' >&2
    printf '  sudo update-locale LANG=%s\n' "$alvo" >&2
    return
  fi

  confirmar "Definir LANG=$alvo para todo o host (idioma e teclado preservados)" || {
    printf 'Locale mantido. Para ajustar depois: sudo localectl set-locale LANG=%s\n' \
      "$alvo"
    return
  }

  executar_privilegiado localectl set-locale "LANG=$alvo" || {
    printf 'Aviso: não foi possível definir o locale; ajuste manualmente.\n' >&2
    return
  }
  printf '  locale:       %s (aplicado; reabra a sessão para valer)\n' "$alvo"
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
  [[ -s "$gerado" ]] || erro "o binário não gerou completion para $shell"

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
      printf '  completion:   não instalado; shell de login não suportado: %s\n' \
        "${shell_login:-desconhecido}"
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

  [[ -n "$valor" ]] || erro "$nome não pode ficar vazio"
  [[ "$valor" != *$'\n'* && "$valor" != *$'\r'* && "$valor" != *"'"* ]] || \
    erro "$nome contém caracteres incompatíveis com o arquivo de ambiente"
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
Uso:
  ./deploy/install-cli.sh
      Instala e configura interativamente o Lucien CLI nativo em Linux.

  ./deploy/install-cli.sh --help
      Exibe esta ajuda.
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
    erro "opção desconhecida: $1"
    ;;
esac

[[ "$(uname -s)" == 'Linux' ]] || erro 'este instalador suporta somente Linux'
case "$(uname -m)" in
  x86_64) arquitetura='amd64' ;;
  aarch64|arm64) arquitetura='arm64' ;;
  *) erro "arquitetura não suportada: $(uname -m)" ;;
esac

exigir_comando install
exigir_comando mktemp
exigir_comando openssl
exigir_comando sha256sum
exigir_comando tar

printf '%s\n' 'Instalação guiada do Lucien CLI para Linux'
printf '%s\n' 'A chave de bootstrap será usada somente em memória, se você optar por criar o primeiro admin.'
printf '%s\n' 'Informe a CA pública gerada no Hub; este cliente nunca cria uma autoridade certificadora.'
printf '%s\n' 'Escopo: 1) usuário atual (~/.local/bin)  2) sistema (/usr/local/bin)'
read -r -p 'Escolha [1]: ' escopo
escopo="${escopo:-1}"

arquivo_pacote_padrao="$(detectar_arquivo_pacote "$arquitetura")"
if [[ -z "$arquivo_pacote_padrao" ]]; then
  arquivo_pacote_padrao="/caminho/lucien_VERSAO_linux_${arquitetura}.tar.gz"
fi
arquivo_pacote="$(perguntar 'Pacote do Lucien CLI acompanhado de checksum' "$arquivo_pacote_padrao")"
[[ -f "$arquivo_pacote" ]] || erro "pacote não encontrado: $arquivo_pacote"
[[ -f "$arquivo_pacote.sha256" ]] || erro "checksum não encontrado: $arquivo_pacote.sha256"

nome_pacote="$(basename -- "$arquivo_pacote")"
[[ "$nome_pacote" =~ ^(lucien_[A-Za-z0-9._-]+_linux_(amd64|arm64))\.tar\.gz$ ]] || \
  erro 'o pacote não segue o padrão lucien_VERSAO_linux_ARQUITETURA.tar.gz'
diretorio_pacote="${BASH_REMATCH[1]}"
[[ "${BASH_REMATCH[2]}" == "$arquitetura" ]] || \
  erro "o pacote não corresponde à arquitetura $arquitetura"

(
  cd -- "$(dirname -- "$arquivo_pacote")"
  sha256sum -c -- "$(basename -- "$arquivo_pacote.sha256")"
)

ca_origem_padrao="$PROJECT_ROOT/certs/ca.crt"
ca_origem="$(perguntar 'Caminho da CA pública do Hub' "$ca_origem_padrao")"
[[ -f "$ca_origem" ]] || \
  erro "CA não encontrada: $ca_origem. Gere os certificados no Hub e copie somente certs/ca.crt para este host"
openssl x509 -in "$ca_origem" -noout >/dev/null 2>&1 || erro 'a CA não contém um certificado PEM válido'
texto_ca="$(openssl x509 -in "$ca_origem" -noout -text)"
[[ "$texto_ca" == *'CA:TRUE'* ]] || erro 'o certificado informado não é uma CA'
[[ "$texto_ca" == *'Certificate Sign'* ]] || \
  erro 'a CA não possui keyUsage para assinatura de certificados'

api_host_padrao="$(ler_api_host_padrao)"
api_host_padrao="${api_host_padrao:-https://localhost:8443}"
api_host="$(perguntar 'URL HTTPS usada pelo CLI para acessar o Hub' "$api_host_padrao")"
validar_valor_shell 'API_HOST' "$api_host"
[[ "$api_host" == https://* ]] || erro 'API_HOST deve começar com https://'
[[ "$api_host" != *'?'* && "$api_host" != *'#'* ]] || \
  erro 'API_HOST não deve conter query string ou fragmento'
autoridade="${api_host#https://}"
autoridade="${autoridade%%/*}"
[[ -n "$autoridade" && "$autoridade" != *'@'* ]] || \
  erro 'API_HOST deve conter um host e não pode incluir credenciais'
api_host="${api_host%/}"

editor_padrao="${EDITOR:-vi}"
editor="$(perguntar 'Editor usado para redigir os runbooks' "$editor_padrao")"
validar_valor_shell 'EDITOR' "$editor"

case "$escopo" in
  1)
    [[ -n "${HOME:-}" ]] || erro 'HOME não está definido para a instalação do usuário'
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
  *) erro 'escopo inválido' ;;
esac

temporario="$(mktemp -d)"
trap 'rm -rf -- "$temporario"' EXIT
tar -xzf "$arquivo_pacote" -C "$temporario" "$diretorio_pacote/lucien"
bin_origem="$temporario/$diretorio_pacote/lucien"
[[ -f "$bin_origem" && ! -L "$bin_origem" ]] || erro 'o pacote não contém um binário regular do Lucien'
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

printf '\nArquivos instalados:\n'
printf '  binário:      %s\n' "$bin_destino"
printf '  CA pública:   %s\n' "$ca_destino"
printf '  ambiente:     %s\n' "$env_file"
if [[ "$escopo" == '1' ]]; then
  instalar_completion_usuario
else
  instalar_completion_sistema
fi
if [[ "$escopo" == '1' ]]; then
  printf '  perfil shell: %s\n' "$profile_file"
fi
configurar_locale_utf8

if confirmar 'Validar agora a conexão TLS com o endpoint /health'; then
  exigir_comando curl
  curl --fail --silent --show-error --cacert "$ca_destino" "$api_host/health"
  printf '\nConexão TLS validada.\n'
fi

if confirmar 'Criar agora o primeiro usuário administrador'; then
  administrador="$(perguntar 'Nome do primeiro administrador' 'administrador')"
  [[ "$administrador" =~ ^[A-Za-z0-9_.-]{3,64}$ ]] || erro 'nome de usuário inválido'
  read -r -s -p 'LUCIEN_BOOTSTRAP_KEY (não será exibida nem salva): ' bootstrap_key
  printf '\n'
  [[ -n "$bootstrap_key" ]] || erro 'a chave de bootstrap não pode ficar vazia'

  permitir_fallback='false'
  printf '%s\n' 'Em servidor sem keyring gráfico, o CLI pode guardar o token em arquivo chmod 600.'
  if confirmar 'Permitir esse fallback somente para salvar o token deste administrador'; then
    permitir_fallback='true'
  fi

  API_HOST="$api_host" \
  TLS_CA_FILE="$ca_destino" \
  EDITOR="$editor" \
  LUCIEN_BOOTSTRAP_KEY="$bootstrap_key" \
  LUCIEN_ALLOW_FILE_TOKEN="$permitir_fallback" \
    "$bin_destino" create user "$administrador"
  unset bootstrap_key

  printf '%s\n' 'Perfil e credencial foram salvos pelo próprio CLI para o usuário que executou este script.'
  printf '%s\n' 'Agora defina USER_CREATION_ENABLED=false no .env do Hub e recrie somente o serviço hub.'
fi

printf '\nPara usar o comando neste terminal, execute:\n  . %q\n' "$env_file"
printf '%s\n' 'Novos terminais de login carregarão a configuração automaticamente.'
