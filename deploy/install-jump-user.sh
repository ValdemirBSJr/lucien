#!/usr/bin/env bash
set -euo pipefail

# Configura a validação automática do Lucien para uma conta individual do jump server.
# Nenhuma credencial é escrita no .bashrc ou neste script.
umask 077

erro() {
  printf 'Erro: %s\n' "$1" >&2
  exit 1
}

[[ "$(uname -s)" == 'Linux' ]] || erro 'este instalador suporta somente Linux'
(( EUID != 0 )) || erro 'execute como a conta individual que usará o Lucien, não como root'
[[ -n "${HOME:-}" && -d "$HOME" ]] || erro 'HOME não está disponível'
command -v lucien >/dev/null 2>&1 || erro 'lucien não foi encontrado no PATH'

config_dir="$HOME/.config/lucien"
hook_file="$config_dir/jump-shell.sh"
bashrc="$HOME/.bashrc"
source_line="[ -r '$hook_file' ] && . '$hook_file'"

read -r -p 'Permitir fallback em arquivo chmod 600 quando o keyring não existir [s/N]: ' resposta
allow_file='false'
if [[ "$resposta" =~ ^([sS]|[sS][iI][mM])$ ]]; then
  allow_file='true'
fi

install -d -m 0700 "$config_dir"
cat > "$hook_file" <<EOF
# Gerado por install-jump-user.sh. Não contém token.
case \$- in
  *i*) ;;
  *) return ;;
esac

if [[ -z "\${LUCIEN_AUTH_ENSURED:-}" && -t 0 && -t 1 ]]; then
  export LUCIEN_AUTH_ENSURED=1
  export LUCIEN_ALLOW_FILE_TOKEN='$allow_file'
  [[ -r "\$HOME/.config/lucien/env" ]] && . "\$HOME/.config/lucien/env"
  lucien auth ensure || printf '%s\n' \
    'Aviso: autenticação Lucien não foi concluída; execute lucien login antes de operações remotas.' >&2
fi
EOF
chmod 0600 "$hook_file"

[[ -e "$bashrc" ]] || install -m 0600 /dev/null "$bashrc"
if ! grep -Fqx -- "$source_line" "$bashrc" 2>/dev/null; then
  printf '\n%s\n' "$source_line" >> "$bashrc"
fi

printf 'Hook instalado em %s\n' "$hook_file"
printf '%s\n' 'Abra uma nova sessão SSH. Se necessário, o token será solicitado sem eco.'
