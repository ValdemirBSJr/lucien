#!/usr/bin/env bash
set -euo pipefail

# Instalação guiada do Runbook API Hub. O CLI não participa desta configuração.
umask 077

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.local.yml"
COMPOSE_BUILD_FILE="$PROJECT_ROOT/docker-compose.build.yml"
SECRETS_DIRECTORY="$PROJECT_ROOT/secrets"
ACT_RUNNER_HOME='/var/lib/act-runner'
ACT_RUNNER_BIN='/usr/local/bin/act_runner'
ACT_RUNNER_UNIT="$PROJECT_ROOT/deploy/systemd/act-runner.service"
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

exigir_valor_dotenv() {
  local nome="$1"
  local valor="$2"

  # O instalador aceita somente valores seguros para uma atribuição dotenv sem aspas.
  [[ "$valor" =~ ^[-A-Za-z0-9._:/@,+=]+$ ]] || \
    erro "$nome contains characters incompatible with the .env file"
}

exigir_nao_vazio() {
  local nome="$1"
  local valor="$2"

  [[ -n "$valor" ]] || erro "$nome is required"
}

exigir_artefato() {
  local caminho="$1"
  local descricao="$2"

  [[ -e "$caminho" ]] || erro "$descricao missing: $caminho"
}

resolver_caminho_host() {
  local caminho="$1"

  if [[ "$caminho" == /* ]]; then
    realpath -m -- "$caminho"
  else
    realpath -m -- "$PROJECT_ROOT/$caminho"
  fi
}

validar_ca_existente() {
  local caminho_resolvido
  caminho_resolvido="$(resolver_caminho_host "$1")"
  [[ -f "$caminho_resolvido" && -r "$caminho_resolvido" ]] || \
    erro "the CA must be a regular, readable file on the host: $caminho_resolvido"
}

preparar_certificados_tls() {
  local certs_dir="$PROJECT_ROOT/certs"
  local arquivo arquivos_presentes=0
  local -a arquivos_obrigatorios=(ca.crt server.crt server.key)

  for arquivo in "${arquivos_obrigatorios[@]}"; do
    [[ -e "$certs_dir/$arquivo" ]] && ((arquivos_presentes += 1))
  done

  if (( arquivos_presentes == 0 )); then
    printf '%s\n' 'TLS certificates missing; generating the CA and the Hub certificate.'
    mkdir -p "$certs_dir"
    (
      cd "$PROJECT_ROOT"
      docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_BUILD_FILE" \
        --profile tools build certgen
      docker compose -f "$COMPOSE_FILE" --profile tools run --rm certgen
    )
    return
  fi

  if (( arquivos_presentes != ${#arquivos_obrigatorios[@]} )); then
    erro 'incomplete TLS set in certs/: keep ca.crt, server.crt and server.key together, or remove the partial artifacts for a fresh issuance'
  fi

  validar_ca_existente "$certs_dir/ca.crt"
  printf '%s\n' 'Existing TLS certificates detected; generation skipped.'
}

validar_url_repositorio_https() {
  local url="$1"
  local autoridade

  [[ "$url" == https://* ]] || erro 'WIKI_REPOSITORY_URL must use HTTPS'
  autoridade="${url#https://}"
  autoridade="${autoridade%%/*}"
  [[ -n "$autoridade" && "$autoridade" != *@* ]] || \
    erro 'WIKI_REPOSITORY_URL must not carry embedded credentials'
}

gerar_segredo() {
  openssl rand -hex 32
}

limitar_cpu() {
  local disponiveis="$1"
  local maximo="$2"

  if (( disponiveis < maximo )); then
    printf '%d.00' "$disponiveis"
  else
    printf '%d.00' "$maximo"
  fi
}

calcular_tag_imagens() {
  local digest

  digest="$(
    cd "$PROJECT_ROOT"
    {
      find backend certgen runbook-viewer secret-scanner wiki-builder \
        -type f ! -name '*.pyc' ! -path '*/__pycache__/*' \
        ! -path '*/.pytest_cache/*' -print0
      printf '%s\0' logo-lucien.png .dockerignore docker-compose.yml docker-compose.build.yml
    } | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16
  )"
  printf 'src-%s' "$digest"
}

instalar_segredo() {
  local nome="$1"
  local valor="$2"
  local destino="$SECRETS_DIRECTORY/$nome"
  local temporario

  [[ ! -e "$destino" ]] || erro "the secret already exists and will not be overwritten: $destino"
  if [[ -z "$valor" ]]; then
    # Vazio só é legítimo quando o preset não usa a integração correspondente e
    # o serviço que consome o secret não está no perfil ativo. Avisar aqui evita
    # que a falha apareça muito depois, no boot, sem apontar para a instalação.
    printf 'Warning: %s came out empty; the service that depends on it will not start until it is filled in.\n' \
      "$nome" >&2
  fi
  temporario="$(mktemp "$SECRETS_DIRECTORY/.${nome}.tmp.XXXXXX")"
  printf '%s' "$valor" > "$temporario"
  # Compose monta secrets originados de arquivo por bind mount e não remapeia
  # UID/GID. O diretório 0700 protege o host; 0444 permite leitura aos UIDs
  # não-root apenas dentro dos contêineres aos quais o secret foi concedido.
  chmod 0444 "$temporario"
  mv -T -- "$temporario" "$destino"
}

uso() {
  cat <<'EOF'
Usage:
  ./deploy/install-hub.sh
      Configures only the Runbook API Hub on this host.

  ./deploy/install-hub.sh --refresh-compose
      Updates docker-compose.local.yml from the base file, keeping a backup.

  ./deploy/install-hub.sh --configure-gitea-runner
      Sets up the advanced-mode act_runner on a dedicated Linux VM.

  ./deploy/install-hub.sh --prepare-nginx-deploy
      Generates the SSH deploy key and the known_hosts on the admin host.
EOF
}

validar_servicos_com_recursos() {
  local compose="$1"
  local sem_limite

  # O Compose operacional exige limite e reserva em todos os serviços. Esta
  # validação evita propagar uma base incompleta para instalações existentes.
  sem_limite="$(awk '
    $0 == "services:" { em_servicos = 1; next }
    em_servicos && /^[^[:space:]#]/ {
      if (servico != "" && !tem_recursos) print servico
      servico = ""
      exit
    }
    em_servicos && /^  [A-Za-z0-9_-]+:[[:space:]]*$/ {
      if (servico != "" && !tem_recursos) print servico
      servico = $1
      sub(/:$/, "", servico)
      tem_recursos = 0
      next
    }
    em_servicos && /^    deploy: \*resources-/ { tem_recursos = 1 }
    END {
      if (servico != "" && !tem_recursos) print servico
    }
  ' "$compose")"

  [[ -z "$sem_limite" ]] || \
    erro "services without limits and reservations in $compose: ${sem_limite//$'\n'/, }"
}

atualizar_compose_local() {
  local compose_base="$PROJECT_ROOT/docker-compose.yml"
  local backup temporario

  exigir_artefato "$compose_base" 'base operational Compose'
  grep -Fqx -- '  upload-worker:' "$compose_base" || \
    erro 'the base operational Compose has no upload-worker service'
  validar_servicos_com_recursos "$compose_base"

  if [[ -f "$COMPOSE_FILE" ]] && cmp -s -- "$compose_base" "$COMPOSE_FILE"; then
    printf '%s\n' 'docker-compose.local.yml is already up to date.'
    return
  fi

  if [[ -e "$COMPOSE_FILE" && ! -f "$COMPOSE_FILE" ]]; then
    erro "$COMPOSE_FILE exists, but is not a regular file"
  fi

  if [[ -f "$COMPOSE_FILE" ]]; then
    backup="$(mktemp "$PROJECT_ROOT/docker-compose.local.yml.bak.XXXXXX")"
    cp -- "$COMPOSE_FILE" "$backup"
    chmod 0600 "$backup"
    printf 'Backup of the previous configuration: %s\n' "$backup"
  fi

  temporario="$(mktemp "$PROJECT_ROOT/.docker-compose.local.yml.tmp.XXXXXX")"
  install -m 0644 "$compose_base" "$temporario"
  mv -T -- "$temporario" "$COMPOSE_FILE"
  printf '%s\n' 'docker-compose.local.yml updated with upload-worker and resource limits for every service.'
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

  erro 'sudo is not available; run this mode directly as root'
}

executar_privilegiado() {
  if (( ${#PRIVILEGE_COMMAND[@]} > 0 )); then
    "${PRIVILEGE_COMMAND[@]}" "$@"
  else
    "$@"
  fi
}

executar_como_runner() {
  if (( EUID == 0 )); then
    exigir_comando runuser
    runuser -u act-runner -- "$@"
  else
    sudo -u act-runner -- "$@"
  fi
}

configurar_gitea_runner() {
  local runner_status

  if [[ -e "$ENV_FILE" || -e "$COMPOSE_FILE" ]]; then
    erro 'this directory already configures a Hub; use a dedicated host for the runner'
  fi

  printf '%s\n' 'Gitea act_runner setup'
  printf '%s\n' 'Warning: Docker access is equivalent to root. Do not use the Hub, SLM, database or Gitea host.'
  confirmar 'Confirm that this host is dedicated exclusively to the runner' || \
    erro 'setup canceled to preserve the isolation of the environment'

  exigir_comando docker
  exigir_comando useradd
  exigir_comando usermod
  exigir_comando getent
  exigir_comando install
  exigir_comando timeout
  [[ -x "$ACT_RUNNER_BIN" ]] || \
    erro "install and validate a pinned act_runner version at $ACT_RUNNER_BIN"
  getent group docker >/dev/null 2>&1 || erro 'the docker group does not exist'
  [[ -f "$ACT_RUNNER_UNIT" ]] || erro 'act_runner systemd template not found'
  preparar_privilegios
  executar_privilegiado docker info >/dev/null 2>&1 || \
    erro 'the Docker daemon is not reachable'

  if ! id -u act-runner >/dev/null 2>&1; then
    executar_privilegiado useradd --system \
      --home-dir "$ACT_RUNNER_HOME" --create-home \
      --shell /usr/sbin/nologin act-runner
  fi
  executar_privilegiado usermod -aG docker act-runner
  executar_privilegiado install -d -o act-runner -g act-runner -m 0700 \
    "$ACT_RUNNER_HOME"

  if [[ ! -f "$ACT_RUNNER_HOME/config.yaml" ]]; then
    executar_como_runner sh -c \
      'cd /var/lib/act-runner && umask 077 && /usr/local/bin/act_runner generate-config > config.yaml'
  else
    printf '%s\n' 'config.yaml already exists; the installer will not overwrite it.'
  fi

  if [[ ! -f "$ACT_RUNNER_HOME/.runner" ]]; then
    printf '%s\n' 'Registration is interactive so the token is not exposed in shell history or the process list.'
    executar_como_runner sh -c \
      'cd /var/lib/act-runner && /usr/local/bin/act_runner --config config.yaml register'
  else
    printf '%s\n' '.runner already exists; registration preserved.'
  fi
  executar_privilegiado chown act-runner:act-runner \
    "$ACT_RUNNER_HOME/config.yaml" "$ACT_RUNNER_HOME/.runner"
  executar_privilegiado chmod 0600 \
    "$ACT_RUNNER_HOME/config.yaml" "$ACT_RUNNER_HOME/.runner"

  printf '%s\n' 'Validating the daemon in the foreground for up to 10 seconds...'
  set +e
  executar_como_runner sh -c \
    'cd /var/lib/act-runner && timeout --signal=TERM 10s /usr/local/bin/act_runner daemon --config config.yaml'
  runner_status=$?
  set -e
  if [[ "$runner_status" != '0' && "$runner_status" != '124' ]]; then
    erro "act_runner exited with status $runner_status during validation"
  fi

  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    executar_privilegiado install -o root -g root -m 0644 \
      "$ACT_RUNNER_UNIT" /etc/systemd/system/act-runner.service
    executar_privilegiado systemctl daemon-reload
    executar_privilegiado systemctl enable --now act-runner.service
    executar_privilegiado systemctl --no-pager --full status act-runner.service
  else
    printf '%s\n' 'systemd is not active. Register the daemon with the service manager of this distribution.'
    printf '%s\n' "Command: $ACT_RUNNER_BIN daemon --config $ACT_RUNNER_HOME/config.yaml"
  fi
}

preparar_nginx_deploy() {
  exigir_comando ssh-keygen
  exigir_comando ssh-keyscan
  exigir_comando realpath
  [[ -n "${HOME:-}" ]] || erro 'HOME is not set'

  local key_path key_dir key_name project_root_real nginx_host known_hosts
  key_path="$(perguntar 'Absolute path for the deploy key' \
    "$HOME/.config/lucien/deploy/lucien-wiki-deploy")"
  [[ "$key_path" == /* ]] || erro 'use an absolute path for the key'
  key_path="$(realpath -m -- "$key_path")"
  project_root_real="$(CDPATH='' cd -- "$PROJECT_ROOT" && pwd -P)"
  [[ "$key_path" != "$project_root_real"/* ]] || \
    erro 'the private key must not be created inside the repository'
  key_dir="$(dirname -- "$key_path")"
  key_name="$(basename -- "$key_path")"
  install -d -m 0700 "$key_dir"
  key_dir="$(CDPATH='' cd -- "$key_dir" && pwd -P)"
  key_path="$key_dir/$key_name"
  if [[ -e "$key_path" && -e "$key_path.pub" ]]; then
    printf '%s\n' 'The deploy key already exists and will be preserved.'
  elif [[ -e "$key_path" || -e "$key_path.pub" ]]; then
    erro 'the key pair is incomplete; fix it without overwriting credentials'
  else
    ssh-keygen -t ed25519 -a 100 -N '' \
      -C 'lucien-gitea-actions' -f "$key_path"
    chmod 0600 "$key_path"
    chmod 0644 "$key_path.pub"
  fi

  nginx_host="$(perguntar 'SSH FQDN or IPv4 of the Nginx server' 'wiki.example.internal')"
  [[ "$nginx_host" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || \
    erro 'the Nginx host contains invalid characters'
  known_hosts="$key_dir/wiki_known_hosts"
  [[ ! -e "$known_hosts" ]] || \
    erro 'wiki_known_hosts already exists; the installer will not overwrite it'
  if ! ssh-keyscan -T 5 -H "$nginx_host" > "$known_hosts"; then
    rm -f "$known_hosts"
    erro 'could not fetch the SSH public key from Nginx'
  fi
  if [[ ! -s "$known_hosts" ]]; then
    rm -f "$known_hosts"
    erro 'Nginx returned no SSH public key'
  fi
  chmod 0600 "$known_hosts"

  printf '\nFingerprints presented by host %s:\n' "$nginx_host"
  ssh-keygen -lf "$known_hosts"
  printf '%s\n' 'WARNING: confirm these fingerprints over an independent channel before using WIKI_KNOWN_HOSTS.'
  printf '\nGenerated files:\n  private key: %s\n  public key:  %s.pub\n  known_hosts: %s\n' \
    "$key_path" "$key_path" "$known_hosts"
  printf '%s\n' 'Install only the .pub key on the non-root Nginx user, with restrict in authorized_keys.'
  printf '%s\n' 'Register the private key only in the SSH_PRIVATE_KEY secret of the Gitea repository.'
}

case "${1:-}" in
  '') ;;
  --refresh-compose)
    [[ "$#" -eq 1 ]] || erro 'this mode takes no additional arguments'
    atualizar_compose_local
    exit 0
    ;;
  --configure-gitea-runner)
    [[ "$#" -eq 1 ]] || erro 'this mode takes no additional arguments'
    configurar_gitea_runner
    exit 0
    ;;
  --prepare-nginx-deploy)
    [[ "$#" -eq 1 ]] || erro 'this mode takes no additional arguments'
    preparar_nginx_deploy
    exit 0
    ;;
  --help|-h)
    uso
    exit 0
    ;;
  *)
    uso >&2
    erro "opção desconhecida: $1"
    ;;
esac

if [[ ! -f "$PROJECT_ROOT/docker-compose.yml" ]]; then
  erro "docker-compose.yml missing at the repository root. Copy it alongside backend/, certgen/, secret-scanner/ and deploy/ into the isolated Hub package"
fi

exigir_artefato "$COMPOSE_BUILD_FILE" 'local image build file'
exigir_artefato "$PROJECT_ROOT/.dockerignore" 'build context protection'
exigir_artefato "$PROJECT_ROOT/backend/Dockerfile" 'Hub image'
exigir_artefato "$PROJECT_ROOT/certgen/Dockerfile" 'certificate generator image'
exigir_artefato "$PROJECT_ROOT/secret-scanner/Dockerfile" 'secret scanner image'

if [[ -e "$ENV_FILE" || -e "$COMPOSE_FILE" ]]; then
  erro ".env or docker-compose.local.yml already exists; the installer does not overwrite configuration"
fi
if [[ -d "$SECRETS_DIRECTORY" ]] && \
  [[ -n "$(find "$SECRETS_DIRECTORY" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  erro "the secrets/ directory already holds files; the installer does not overwrite credentials"
fi

command -v docker >/dev/null 2>&1 || erro "Docker not found; install it before continuing"
docker compose version >/dev/null 2>&1 || erro "Docker Compose v2 is not available"
docker_cpu_count="$(docker info --format '{{.NCPU}}' 2>/dev/null)" || \
  erro 'could not query the Docker daemon resources'
[[ "$docker_cpu_count" =~ ^[1-9][0-9]*$ ]] || \
  erro "invalid CPU count reported by Docker: $docker_cpu_count"
command -v openssl >/dev/null 2>&1 || erro "OpenSSL not found; it is used to generate secrets"
command -v realpath >/dev/null 2>&1 || erro "realpath not found; install coreutils before continuing"
for command_name in find sort xargs sha256sum install mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || \
    erro "$command_name was not found; install coreutils/findutils before continuing"
done

printf '%s\n' 'Guided installation of the Runbook API Hub'
printf '%s\n' 'This creates .env (mode 0600) and an editable docker-compose.local.yml.'
printf '%s\n' 'No token is shown in the terminal.'

hub_dns="$(perguntar 'FQDN clients will use to reach the Hub' 'runbook.example.internal')"
[[ "$hub_dns" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || \
  erro 'the FQDN contains invalid characters'

if confirmar 'Expose HTTPS to the network on TCP/8443'; then
  hub_bind_address='0.0.0.0'
else
  hub_bind_address='127.0.0.1'
fi

cert_ip="$(perguntar 'Additional IP for the certificate SAN' '127.0.0.1')"
[[ "$cert_ip" =~ ^[0-9A-Fa-f:.]+$ ]] || erro 'the certificate IP contains invalid characters'

if confirmar 'Run the Ollama SLM on this same machine'; then
  compose_profile='consolidated'
  slm_base_url='http://slm:11434'
else
  compose_profile='server'
  slm_base_url="$(perguntar 'Internal URL of the remote SLM' 'http://slm.example.internal:11434')"
  exigir_valor_dotenv 'SLM_BASE_URL' "$slm_base_url"
fi

slm_model="$(perguntar 'SLM model' 'qwen2.5-coder:3b')"
exigir_valor_dotenv 'SLM_MODEL' "$slm_model"
slm_language_runbook="$(perguntar 'Runbook language (pt-br or en)' 'pt-br')"
case "$slm_language_runbook" in
  pt-br|en) ;;
  *) erro 'the runbook language must be pt-br or en' ;;
esac

cat <<'EOF'
Publication and viewing mode:
  1) local-viewer  - local disk + HTTPS portal with controlled review on TCP/9091
  2) github        - GitHub-hosted Actions + GitHub Pages, no self-hosted runner
  3) gitea-compact - fixed builder + Nginx on this host, no Docker socket
  4) gitea-runner  - Gitea Actions on a dedicated VM (advanced mode)
EOF
read -r -p 'Choose [1]: ' publication_choice
publication_choice="${publication_choice:-1}"

publication_mode='local-viewer'
compose_profiles="$compose_profile"
storage_provider='local'
local_storage_root='/data/playbooks'
git_api_base='https://api.github.com'
git_owner=''
git_repo=''
git_branch='main'
git_docs_prefix='docs/runbooks'
git_token=''
git_ca_source='./certs/ca.crt'
viewer_bind_address='127.0.0.1'
# Chave de sessão do portal: é aleatória e local, sem dependência externa, então
# é sempre gerada. Deixá-la vazia para presets sem portal criava um secret de
# zero byte que só falhava muito depois, no boot do runbook-viewer.
viewer_session_secret="$(gerar_segredo)"
wiki_repository_url=''
wiki_repository_user=''
wiki_repository_token=''
wiki_bind_address='127.0.0.1'
configurar_git='false'

case "$publication_choice" in
  1)
    compose_profiles="$compose_profile,local-viewer"
    exigir_artefato "$PROJECT_ROOT/runbook-viewer/Dockerfile" \
      'local viewer image'
    exigir_artefato "$PROJECT_ROOT/logo-lucien.png" 'local viewer logo'
    if confirmar 'Expose the HTTPS viewer to the network on TCP/9091'; then
      viewer_bind_address='0.0.0.0'
    fi
    ;;
  2)
    publication_mode='github'
    storage_provider='github'
    configurar_git='true'
    cat <<'EOF'
WARNING: a private repository does not make the Pages site private. On
GitHub.com, publishing from a private repository depends on the plan; private
access to the site requires an organization on GitHub Enterprise Cloud. In any
other scenario the site may end up public even when the source code is private.
The workflow shipped here does not support GitHub Enterprise Server.
EOF
    confirmar 'Confirm that the plan and the site visibility have been validated' || \
      erro 'GitHub mode canceled to avoid publishing with the wrong visibility'
    ;;
  3)
    publication_mode='gitea-compact'
    compose_profiles="$compose_profile,gitea-compact"
    storage_provider='gitea'
    configurar_git='true'
    exigir_artefato "$PROJECT_ROOT/wiki-builder/Dockerfile" \
      'fixed builder image'
    exigir_artefato "$PROJECT_ROOT/deploy/nginx/wiki-compact.conf" \
      'compact mode Nginx configuration'
    ;;
  4)
    publication_mode='gitea-runner'
    storage_provider='gitea'
    configurar_git='true'
    ;;
  *) erro 'invalid publication mode' ;;
esac

if [[ "$configurar_git" == 'true' ]]; then
  if [[ "$storage_provider" == 'gitea' ]]; then
    git_api_base="$(perguntar 'Gitea API base' 'https://gitea.example.internal/api/v1')"
  fi
  git_owner="$(perguntar 'Repository organization or owner' 'infrastructure')"
  git_repo="$(perguntar 'Documentation repository name' 'runbooks')"
  git_branch="$(perguntar 'Publication branch' 'main')"
  git_docs_prefix="$(perguntar 'MkDocs directory for the runbooks' 'docs/runbooks')"
  read -r -s -p 'Git publication token (never shown): ' git_token
  printf '\n'

  exigir_nao_vazio 'GIT_TOKEN' "$git_token"
  exigir_valor_dotenv 'GIT_API_BASE' "$git_api_base"
  exigir_valor_dotenv 'GIT_OWNER' "$git_owner"
  exigir_valor_dotenv 'GIT_REPO' "$git_repo"
  exigir_valor_dotenv 'GIT_BRANCH' "$git_branch"
  exigir_valor_dotenv 'GIT_DOCS_PREFIX' "$git_docs_prefix"
fi

if [[ "$storage_provider" == 'gitea' ]]; then
  git_ca_source="$(perguntar 'Public CA used to validate Gitea on the host' './certs/ca.crt')"
  exigir_valor_dotenv 'GIT_CA_SOURCE' "$git_ca_source"
  if [[ "$git_ca_source" != './certs/ca.crt' ]]; then
    git_ca_source="$(resolver_caminho_host "$git_ca_source")"
    validar_ca_existente "$git_ca_source"
  elif [[ -e "$PROJECT_ROOT/certs/ca.crt" ]]; then
    validar_ca_existente "$git_ca_source"
  fi
fi

if [[ "$publication_mode" == 'gitea-compact' ]]; then
  gitea_web_base="${git_api_base%/}"
  gitea_web_base="${gitea_web_base%/api/v1}"
  wiki_repository_url="$(perguntar 'HTTPS clone URL of the repository' \
    "$gitea_web_base/$git_owner/$git_repo.git")"
  wiki_repository_user="$(perguntar 'Read-only service user for the builder' \
    'lucien-builder')"
  read -r -s -p 'Separate read-only builder token (never shown): ' \
    wiki_repository_token
  printf '\n'

  exigir_nao_vazio 'WIKI_REPOSITORY_TOKEN' "$wiki_repository_token"
  validar_url_repositorio_https "$wiki_repository_url"
  exigir_valor_dotenv 'WIKI_REPOSITORY_URL' "$wiki_repository_url"
  exigir_valor_dotenv 'WIKI_REPOSITORY_USER' "$wiki_repository_user"
fi

user_creation_enabled='false'
if confirmar 'Open the temporary window to create the first administrator'; then
  user_creation_enabled='true'
fi

postgres_password="$(gerar_segredo)"
bootstrap_api_key="$(gerar_segredo)"
auth_pepper="$(gerar_segredo)"
lucien_image_tag="$(calcular_tag_imagens)"
lucien_tiny_cpu_limit='0.50'
lucien_small_cpu_limit="$(limitar_cpu "$docker_cpu_count" 1)"
lucien_medium_cpu_limit="$(limitar_cpu "$docker_cpu_count" 2)"
lucien_slm_cpu_limit="$(limitar_cpu "$docker_cpu_count" 4)"
temporary_env="$(mktemp "$PROJECT_ROOT/.env.tmp.XXXXXX")"
trap 'rm -f "$temporary_env"' EXIT

cat > "$temporary_env" <<EOF
# Gerado por deploy/install-hub.sh. Não versione este arquivo.
COMPOSE_PROFILES=$compose_profiles
API_HOST=https://$hub_dns:8443
TLS_CA_FILE=/certs/ca.crt
CERTS_DIR=./certs
SECRETS_DIR=./secrets
LUCIEN_IMAGE_TAG=$lucien_image_tag
LUCIEN_TINY_CPU_LIMIT=$lucien_tiny_cpu_limit
LUCIEN_SMALL_CPU_LIMIT=$lucien_small_cpu_limit
LUCIEN_MEDIUM_CPU_LIMIT=$lucien_medium_cpu_limit
LUCIEN_SLM_CPU_LIMIT=$lucien_slm_cpu_limit
HUB_BIND_ADDRESS=$hub_bind_address

VIEWER_BIND_ADDRESS=$viewer_bind_address
VIEWER_HUB_URL=https://hub:8443
VIEWER_SESSION_TTL_SECONDS=900
VIEWER_MAX_DOCUMENTS=10000
VIEWER_MAX_FILE_BYTES=1048576

USER_CREATION_ENABLED=$user_creation_enabled

POSTGRES_DB=lucien
POSTGRES_USER=lucien

SLM_BASE_URL=$slm_base_url
SLM_MODEL=$slm_model
SLM_LANGUAGE_RUNBOOK=$slm_language_runbook
SLM_TIMEOUT_SECONDS=300
UPLOAD_WORKER_POLL_SECONDS=2
UPLOAD_WORKER_LEASE_SECONDS=900
UPLOAD_WORKER_RETRY_BASE_SECONDS=10
UPLOAD_WORKER_MAX_ATTEMPTS=5
MAX_LOG_BYTES=2097152
SECRET_SCANNER_URL=http://secret-scanner:8090
SECRET_SCANNER_TIMEOUT_SECONDS=5

STORAGE_PROVIDER=$storage_provider
LOCAL_STORAGE_ROOT=$local_storage_root
GIT_API_BASE=$git_api_base
GIT_OWNER=$git_owner
GIT_REPO=$git_repo
GIT_BRANCH=$git_branch
GIT_DOCS_PREFIX=$git_docs_prefix
GIT_CA_SOURCE=$git_ca_source

WIKI_REPOSITORY_URL=$wiki_repository_url
WIKI_REPOSITORY_BRANCH=$git_branch
WIKI_REPOSITORY_USER=$wiki_repository_user
WIKI_POLL_SECONDS=60
WIKI_BUILD_TIMEOUT_SECONDS=120
WIKI_MAX_REPOSITORY_BYTES=536870912
WIKI_MAX_SOURCE_FILES=10000
WIKI_MAX_SOURCE_BYTES=268435456
WIKI_MAX_FILE_BYTES=1048576
WIKI_RELEASE_RETENTION=5
WIKI_BIND_ADDRESS=$wiki_bind_address

CERT_DNS=$hub_dns,hub,localhost
CERT_IP=$cert_ip
EOF

install -d -m 0700 "$SECRETS_DIRECTORY"
for secret_name in postgres_password database_url bootstrap_api_key auth_pepper \
  git_token viewer_session_secret wiki_repository_token; do
  [[ ! -e "$SECRETS_DIRECTORY/$secret_name" ]] || \
    erro "the secret already exists and will not be overwritten: $SECRETS_DIRECTORY/$secret_name"
done
instalar_segredo postgres_password "$postgres_password"
instalar_segredo database_url \
  "postgresql+asyncpg://lucien:$postgres_password@postgres:5432/lucien"
instalar_segredo bootstrap_api_key "$bootstrap_api_key"
instalar_segredo auth_pepper "$auth_pepper"
instalar_segredo git_token "$git_token"
instalar_segredo viewer_session_secret "$viewer_session_secret"
instalar_segredo wiki_repository_token "$wiki_repository_token"
install -m 0600 "$temporary_env" "$ENV_FILE"
cp "$PROJECT_ROOT/docker-compose.yml" "$COMPOSE_FILE"
chmod 0644 "$COMPOSE_FILE"
trap - EXIT
rm -f "$temporary_env"

printf '\nCreated files:\n  %s\n  %s\n  %s/ (mode 0700; files 0444)\n' \
  "$ENV_FILE" "$COMPOSE_FILE" "$SECRETS_DIRECTORY"
printf '%s\n' 'Edit docker-compose.local.yml if you need administrator-specific adjustments.'

preparar_certificados_tls

if confirmar 'Bring the Hub up now'; then
  # Impede que o Docker crie um diretório quando a origem de CA estiver ausente.
  validar_ca_existente "$git_ca_source"
  (
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_BUILD_FILE" build
    docker compose -f "$COMPOSE_FILE" up -d
  )
fi

if [[ "$user_creation_enabled" == 'true' ]]; then
  printf '%s\n' 'Bootstrap enabled: create only the first administrator, on a controlled host.'
  printf '%s\n' 'On Linux, install and configure the client separately: ./deploy/install-cli.sh'
  printf '%s\n' 'Then set USER_CREATION_ENABLED to false in .env and recreate the Hub only.'
else
  printf '%s\n' 'Bootstrap disabled. Open USER_CREATION_ENABLED only during the controlled creation of the first administrator.'
fi

case "$publication_mode" in
  local-viewer)
    if [[ "$viewer_bind_address" == '127.0.0.1' ]]; then
      viewer_access_host='localhost'
    else
      viewer_access_host="$hub_dns"
    fi
    printf '\nLocal viewer configured at https://%s:9091.\n' "$viewer_access_host"
    printf '%s\n' 'Access requires a username and a personal token; admin and senior can create immutable revisions through the Hub.'
    printf '%s\n' 'Senior is limited to its own domain; the portal volume stays read-only.'
    ;;
  github)
    printf '\nGitHub Pages selected: use the hosted workflow .github/workflows/deploy.yml.\n'
    printf '%s\n' 'Under Settings > Pages, select GitHub Actions; no self-hosted runner is needed.'
    ;;
  gitea-compact)
    printf '\nCompact Gitea mode configured on local port TCP/9092.\n'
    printf '%s\n' 'The fixed builder uses no Docker socket and runs no workflows from the repository.'
    printf '%s\n' 'For remote access, keep the local bind and put an authenticated HTTPS proxy in front.'
    ;;
  gitea-runner)
    printf '\nAfter creating the admin and closing the bootstrap, set up advanced mode on separate hosts:\n'
    printf '  Dedicated runner host: %s\n' './deploy/install-hub.sh --configure-gitea-runner'
    printf '  Administrative host:   %s\n' './deploy/install-hub.sh --prepare-nginx-deploy'
    ;;
esac
