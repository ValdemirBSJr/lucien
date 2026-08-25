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

exigir_valor_dotenv() {
  local nome="$1"
  local valor="$2"

  # O instalador aceita somente valores seguros para uma atribuição dotenv sem aspas.
  [[ "$valor" =~ ^[-A-Za-z0-9._:/@,+=]+$ ]] || \
    erro "$nome contém caracteres incompatíveis com o arquivo .env"
}

exigir_nao_vazio() {
  local nome="$1"
  local valor="$2"

  [[ -n "$valor" ]] || erro "$nome é obrigatório"
}

exigir_artefato() {
  local caminho="$1"
  local descricao="$2"

  [[ -e "$caminho" ]] || erro "$descricao ausente: $caminho"
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
    erro "a CA deve ser um arquivo regular legível no host: $caminho_resolvido"
}

preparar_certificados_tls() {
  local certs_dir="$PROJECT_ROOT/certs"
  local arquivo arquivos_presentes=0
  local -a arquivos_obrigatorios=(ca.crt server.crt server.key)

  for arquivo in "${arquivos_obrigatorios[@]}"; do
    [[ -e "$certs_dir/$arquivo" ]] && ((arquivos_presentes += 1))
  done

  if (( arquivos_presentes == 0 )); then
    printf '%s\n' 'Certificados TLS ausentes; gerando CA e certificado do Hub.'
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
    erro 'conjunto TLS incompleto em certs/: mantenha ca.crt, server.crt e server.key juntos ou remova os artefatos parciais para uma nova emissão'
  fi

  validar_ca_existente "$certs_dir/ca.crt"
  printf '%s\n' 'Certificados TLS existentes detectados; geração ignorada.'
}

validar_url_repositorio_https() {
  local url="$1"
  local autoridade

  [[ "$url" == https://* ]] || erro 'WIKI_REPOSITORY_URL deve usar HTTPS'
  autoridade="${url#https://}"
  autoridade="${autoridade%%/*}"
  [[ -n "$autoridade" && "$autoridade" != *@* ]] || \
    erro 'WIKI_REPOSITORY_URL não pode conter credenciais embutidas'
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

  [[ ! -e "$destino" ]] || erro "o segredo já existe e não será sobrescrito: $destino"
  if [[ -z "$valor" ]]; then
    # Vazio só é legítimo quando o preset não usa a integração correspondente e
    # o serviço que consome o secret não está no perfil ativo. Avisar aqui evita
    # que a falha apareça muito depois, no boot, sem apontar para a instalação.
    printf 'Aviso: %s ficou vazio; o serviço que depende dele não iniciará até ser preenchido.\n' \
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
Uso:
  ./deploy/install-hub.sh
      Configura somente o Runbook API Hub neste host.

  ./deploy/install-hub.sh --refresh-compose
      Atualiza docker-compose.local.yml a partir da base e preserva um backup.

  ./deploy/install-hub.sh --configure-gitea-runner
      Configura o act_runner do modo avançado em uma VM Linux dedicada.

  ./deploy/install-hub.sh --prepare-nginx-deploy
      Gera a chave SSH de deploy e o known_hosts no host administrativo.
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
    erro "serviços sem limites e reservas em $compose: ${sem_limite//$'\n'/, }"
}

atualizar_compose_local() {
  local compose_base="$PROJECT_ROOT/docker-compose.yml"
  local backup temporario

  exigir_artefato "$compose_base" 'Compose operacional base'
  grep -Fqx -- '  upload-worker:' "$compose_base" || \
    erro 'o Compose operacional base não contém o serviço upload-worker'
  validar_servicos_com_recursos "$compose_base"

  if [[ -f "$COMPOSE_FILE" ]] && cmp -s -- "$compose_base" "$COMPOSE_FILE"; then
    printf '%s\n' 'docker-compose.local.yml já está atualizado.'
    return
  fi

  if [[ -e "$COMPOSE_FILE" && ! -f "$COMPOSE_FILE" ]]; then
    erro "$COMPOSE_FILE existe, mas não é um arquivo regular"
  fi

  if [[ -f "$COMPOSE_FILE" ]]; then
    backup="$(mktemp "$PROJECT_ROOT/docker-compose.local.yml.bak.XXXXXX")"
    cp -- "$COMPOSE_FILE" "$backup"
    chmod 0600 "$backup"
    printf 'Backup da configuração anterior: %s\n' "$backup"
  fi

  temporario="$(mktemp "$PROJECT_ROOT/.docker-compose.local.yml.tmp.XXXXXX")"
  install -m 0644 "$compose_base" "$temporario"
  mv -T -- "$temporario" "$COMPOSE_FILE"
  printf '%s\n' 'docker-compose.local.yml atualizado com upload-worker e limites de recursos para todos os serviços.'
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

  erro 'sudo não está disponível; execute este modo diretamente como root'
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
    erro 'este diretório já configura um Hub; use um host dedicado para o runner'
  fi

  printf '%s\n' 'Configuração do Gitea act_runner'
  printf '%s\n' 'Atenção: acesso ao Docker equivale a root. Não use o host do Hub, SLM, banco ou Gitea.'
  confirmar 'Confirma que este é um host dedicado exclusivamente ao runner' || \
    erro 'configuração cancelada para preservar o isolamento do ambiente'

  exigir_comando docker
  exigir_comando useradd
  exigir_comando usermod
  exigir_comando getent
  exigir_comando install
  exigir_comando timeout
  [[ -x "$ACT_RUNNER_BIN" ]] || \
    erro "instale e valide uma versão fixa do act_runner em $ACT_RUNNER_BIN"
  getent group docker >/dev/null 2>&1 || erro 'o grupo docker não existe'
  [[ -f "$ACT_RUNNER_UNIT" ]] || erro 'template systemd do act_runner não encontrado'
  preparar_privilegios
  executar_privilegiado docker info >/dev/null 2>&1 || \
    erro 'o daemon Docker não está acessível'

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
    printf '%s\n' 'config.yaml já existe; o instalador não irá sobrescrevê-lo.'
  fi

  if [[ ! -f "$ACT_RUNNER_HOME/.runner" ]]; then
    printf '%s\n' 'O registro é interativo para não expor o token no histórico ou na lista de processos.'
    executar_como_runner sh -c \
      'cd /var/lib/act-runner && /usr/local/bin/act_runner --config config.yaml register'
  else
    printf '%s\n' '.runner já existe; registro preservado.'
  fi
  executar_privilegiado chown act-runner:act-runner \
    "$ACT_RUNNER_HOME/config.yaml" "$ACT_RUNNER_HOME/.runner"
  executar_privilegiado chmod 0600 \
    "$ACT_RUNNER_HOME/config.yaml" "$ACT_RUNNER_HOME/.runner"

  printf '%s\n' 'Validando o daemon em primeiro plano por até 10 segundos...'
  set +e
  executar_como_runner sh -c \
    'cd /var/lib/act-runner && timeout --signal=TERM 10s /usr/local/bin/act_runner daemon --config config.yaml'
  runner_status=$?
  set -e
  if [[ "$runner_status" != '0' && "$runner_status" != '124' ]]; then
    erro "o act_runner encerrou com status $runner_status durante a validação"
  fi

  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    executar_privilegiado install -o root -g root -m 0644 \
      "$ACT_RUNNER_UNIT" /etc/systemd/system/act-runner.service
    executar_privilegiado systemctl daemon-reload
    executar_privilegiado systemctl enable --now act-runner.service
    executar_privilegiado systemctl --no-pager --full status act-runner.service
  else
    printf '%s\n' 'systemd não está ativo. Cadastre o daemon no gerenciador de serviços desta distribuição.'
    printf '%s\n' "Comando: $ACT_RUNNER_BIN daemon --config $ACT_RUNNER_HOME/config.yaml"
  fi
}

preparar_nginx_deploy() {
  exigir_comando ssh-keygen
  exigir_comando ssh-keyscan
  exigir_comando realpath
  [[ -n "${HOME:-}" ]] || erro 'HOME não está definido'

  local key_path key_dir key_name project_root_real nginx_host known_hosts
  key_path="$(perguntar 'Caminho absoluto da chave de deploy' \
    "$HOME/.config/lucien/deploy/lucien-wiki-deploy")"
  [[ "$key_path" == /* ]] || erro 'use um caminho absoluto para a chave'
  key_path="$(realpath -m -- "$key_path")"
  project_root_real="$(CDPATH='' cd -- "$PROJECT_ROOT" && pwd -P)"
  [[ "$key_path" != "$project_root_real"/* ]] || \
    erro 'a chave privada não pode ser criada dentro do repositório'
  key_dir="$(dirname -- "$key_path")"
  key_name="$(basename -- "$key_path")"
  install -d -m 0700 "$key_dir"
  key_dir="$(CDPATH='' cd -- "$key_dir" && pwd -P)"
  key_path="$key_dir/$key_name"
  if [[ -e "$key_path" && -e "$key_path.pub" ]]; then
    printf '%s\n' 'A chave de deploy já existe e será preservada.'
  elif [[ -e "$key_path" || -e "$key_path.pub" ]]; then
    erro 'o par de chaves está incompleto; corrija-o sem sobrescrever credenciais'
  else
    ssh-keygen -t ed25519 -a 100 -N '' \
      -C 'lucien-gitea-actions' -f "$key_path"
    chmod 0600 "$key_path"
    chmod 0644 "$key_path.pub"
  fi

  nginx_host="$(perguntar 'FQDN ou IPv4 SSH do servidor Nginx' 'wiki.exemplo.interno')"
  [[ "$nginx_host" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || \
    erro 'o host Nginx contém caracteres inválidos'
  known_hosts="$key_dir/wiki_known_hosts"
  [[ ! -e "$known_hosts" ]] || \
    erro 'wiki_known_hosts já existe; o instalador não irá sobrescrevê-lo'
  if ! ssh-keyscan -T 5 -H "$nginx_host" > "$known_hosts"; then
    rm -f "$known_hosts"
    erro 'não foi possível obter a chave pública SSH do Nginx'
  fi
  if [[ ! -s "$known_hosts" ]]; then
    rm -f "$known_hosts"
    erro 'o Nginx não retornou uma chave pública SSH'
  fi
  chmod 0600 "$known_hosts"

  printf '\nFingerprints apresentados pelo host %s:\n' "$nginx_host"
  ssh-keygen -lf "$known_hosts"
  printf '%s\n' 'ATENÇÃO: confirme esses fingerprints por um canal independente antes de usar WIKI_KNOWN_HOSTS.'
  printf '\nArquivos gerados:\n  chave privada: %s\n  chave pública: %s.pub\n  known_hosts: %s\n' \
    "$key_path" "$key_path" "$known_hosts"
  printf '%s\n' 'Instale somente a chave .pub no usuário não-root do Nginx, com restrict no authorized_keys.'
  printf '%s\n' 'Cadastre a chave privada somente no segredo SSH_PRIVATE_KEY do repositório Gitea.'
}

case "${1:-}" in
  '') ;;
  --refresh-compose)
    [[ "$#" -eq 1 ]] || erro 'este modo não aceita argumentos adicionais'
    atualizar_compose_local
    exit 0
    ;;
  --configure-gitea-runner)
    [[ "$#" -eq 1 ]] || erro 'este modo não aceita argumentos adicionais'
    configurar_gitea_runner
    exit 0
    ;;
  --prepare-nginx-deploy)
    [[ "$#" -eq 1 ]] || erro 'este modo não aceita argumentos adicionais'
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
  erro "docker-compose.yml ausente na raiz. Copie esse arquivo junto de backend/, certgen/, secret-scanner/ e deploy/ para o pacote isolado do Hub"
fi

exigir_artefato "$COMPOSE_BUILD_FILE" 'arquivo de build local das imagens'
exigir_artefato "$PROJECT_ROOT/.dockerignore" 'proteção do contexto de build'
exigir_artefato "$PROJECT_ROOT/backend/Dockerfile" 'imagem do Hub'
exigir_artefato "$PROJECT_ROOT/certgen/Dockerfile" 'imagem do gerador de certificados'
exigir_artefato "$PROJECT_ROOT/secret-scanner/Dockerfile" 'imagem do scanner de segredos'

if [[ -e "$ENV_FILE" || -e "$COMPOSE_FILE" ]]; then
  erro ".env ou docker-compose.local.yml já existe; o instalador não sobrescreve configurações"
fi
if [[ -d "$SECRETS_DIRECTORY" ]] && \
  [[ -n "$(find "$SECRETS_DIRECTORY" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  erro "o diretório secrets/ já contém arquivos; o instalador não sobrescreve credenciais"
fi

command -v docker >/dev/null 2>&1 || erro "Docker não encontrado; instale-o antes de continuar"
docker compose version >/dev/null 2>&1 || erro "Docker Compose v2 não está disponível"
docker_cpu_count="$(docker info --format '{{.NCPU}}' 2>/dev/null)" || \
  erro 'não foi possível consultar os recursos do daemon Docker'
[[ "$docker_cpu_count" =~ ^[1-9][0-9]*$ ]] || \
  erro "quantidade de CPUs inválida informada pelo Docker: $docker_cpu_count"
command -v openssl >/dev/null 2>&1 || erro "OpenSSL não encontrado; ele é usado para gerar segredos"
command -v realpath >/dev/null 2>&1 || erro "realpath não encontrado; instale coreutils antes de continuar"
for command_name in find sort xargs sha256sum install mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || \
    erro "$command_name não foi encontrado; instale coreutils/findutils antes de continuar"
done

printf '%s\n' 'Instalação guiada do Runbook API Hub'
printf '%s\n' 'Serão criados .env (modo 0600) e docker-compose.local.yml editável.'
printf '%s\n' 'Nenhum token será exibido no terminal.'

hub_dns="$(perguntar 'FQDN que os clientes usarão para acessar o Hub' 'runbook.exemplo.interno')"
[[ "$hub_dns" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || \
  erro 'o FQDN contém caracteres inválidos'

if confirmar 'Expor HTTPS para a rede em TCP/8443'; then
  hub_bind_address='0.0.0.0'
else
  hub_bind_address='127.0.0.1'
fi

cert_ip="$(perguntar 'IP adicional para o SAN do certificado' '127.0.0.1')"
[[ "$cert_ip" =~ ^[0-9A-Fa-f:.]+$ ]] || erro 'o IP do certificado contém caracteres inválidos'

if confirmar 'Executar a SLM Ollama nesta mesma máquina'; then
  compose_profile='consolidated'
  slm_base_url='http://slm:11434'
else
  compose_profile='server'
  slm_base_url="$(perguntar 'URL interna da SLM remota' 'http://slm.exemplo.interno:11434')"
  exigir_valor_dotenv 'SLM_BASE_URL' "$slm_base_url"
fi

slm_model="$(perguntar 'Modelo da SLM' 'qwen2.5-coder:3b')"
exigir_valor_dotenv 'SLM_MODEL' "$slm_model"
slm_language_runbook="$(perguntar 'Idioma dos runbooks (pt-br ou en)' 'pt-br')"
case "$slm_language_runbook" in
  pt-br|en) ;;
  *) erro 'o idioma dos runbooks deve ser pt-br ou en' ;;
esac

cat <<'EOF'
Modo de publicação e visualização:
  1) local-viewer  - disco local + portal HTTPS com revisão controlada em TCP/9091
  2) github        - GitHub-hosted Actions + GitHub Pages, sem runner próprio
  3) gitea-compact - builder fixo + Nginx neste host, sem Docker socket
  4) gitea-runner  - Gitea Actions em VM dedicada (modo avançado)
EOF
read -r -p 'Escolha [1]: ' publication_choice
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
      'imagem do visualizador local'
    exigir_artefato "$PROJECT_ROOT/logo-lucien.png" 'logo do visualizador local'
    if confirmar 'Expor o visualizador HTTPS para a rede em TCP/9091'; then
      viewer_bind_address='0.0.0.0'
    fi
    ;;
  2)
    publication_mode='github'
    storage_provider='github'
    configurar_git='true'
    cat <<'EOF'
ATENÇÃO: repositório privado não torna o site Pages privado. No GitHub.com, a
publicação a partir de repositório privado depende do plano; acesso privado ao
site exige uma organização no GitHub Enterprise Cloud. Nos demais cenários o
site pode ficar público mesmo que o código-fonte seja privado. O workflow
entregue não suporta GitHub Enterprise Server.
EOF
    confirmar 'Confirma que o plano e a visibilidade do site foram validados' || \
      erro 'modo GitHub cancelado para evitar publicação com visibilidade incorreta'
    ;;
  3)
    publication_mode='gitea-compact'
    compose_profiles="$compose_profile,gitea-compact"
    storage_provider='gitea'
    configurar_git='true'
    exigir_artefato "$PROJECT_ROOT/wiki-builder/Dockerfile" \
      'imagem do builder fixo'
    exigir_artefato "$PROJECT_ROOT/deploy/nginx/wiki-compact.conf" \
      'configuração Nginx do modo compacto'
    ;;
  4)
    publication_mode='gitea-runner'
    storage_provider='gitea'
    configurar_git='true'
    ;;
  *) erro 'modo de publicação inválido' ;;
esac

if [[ "$configurar_git" == 'true' ]]; then
  if [[ "$storage_provider" == 'gitea' ]]; then
    git_api_base="$(perguntar 'Base da API do Gitea' 'https://gitea.exemplo.interno/api/v1')"
  fi
  git_owner="$(perguntar 'Organização ou proprietário do repositório' 'infraestrutura')"
  git_repo="$(perguntar 'Nome do repositório de documentação' 'runbooks')"
  git_branch="$(perguntar 'Branch de publicação' 'main')"
  git_docs_prefix="$(perguntar 'Diretório MkDocs dos runbooks' 'docs/runbooks')"
  read -r -s -p 'Token de publicação Git (não será exibido): ' git_token
  printf '\n'

  exigir_nao_vazio 'GIT_TOKEN' "$git_token"
  exigir_valor_dotenv 'GIT_API_BASE' "$git_api_base"
  exigir_valor_dotenv 'GIT_OWNER' "$git_owner"
  exigir_valor_dotenv 'GIT_REPO' "$git_repo"
  exigir_valor_dotenv 'GIT_BRANCH' "$git_branch"
  exigir_valor_dotenv 'GIT_DOCS_PREFIX' "$git_docs_prefix"
fi

if [[ "$storage_provider" == 'gitea' ]]; then
  git_ca_source="$(perguntar 'CA pública usada para validar o Gitea no host' './certs/ca.crt')"
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
  wiki_repository_url="$(perguntar 'URL HTTPS de clone do repositório' \
    "$gitea_web_base/$git_owner/$git_repo.git")"
  wiki_repository_user="$(perguntar 'Usuário de serviço somente leitura do builder' \
    'lucien-builder')"
  read -r -s -p 'Token separado, somente leitura, do builder (não será exibido): ' \
    wiki_repository_token
  printf '\n'

  exigir_nao_vazio 'WIKI_REPOSITORY_TOKEN' "$wiki_repository_token"
  validar_url_repositorio_https "$wiki_repository_url"
  exigir_valor_dotenv 'WIKI_REPOSITORY_URL' "$wiki_repository_url"
  exigir_valor_dotenv 'WIKI_REPOSITORY_USER' "$wiki_repository_user"
fi

user_creation_enabled='false'
if confirmar 'Abrir a janela temporária para criar o primeiro administrador'; then
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
    erro "o segredo já existe e não será sobrescrito: $SECRETS_DIRECTORY/$secret_name"
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

printf '\nArquivos criados:\n  %s\n  %s\n  %s/ (modo 0700; arquivos 0444)\n' \
  "$ENV_FILE" "$COMPOSE_FILE" "$SECRETS_DIRECTORY"
printf '%s\n' 'Edite docker-compose.local.yml se precisar de ajustes específicos do administrador.'

preparar_certificados_tls

if confirmar 'Subir o Hub agora'; then
  # Impede que o Docker crie um diretório quando a origem de CA estiver ausente.
  validar_ca_existente "$git_ca_source"
  (
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_BUILD_FILE" build
    docker compose -f "$COMPOSE_FILE" up -d
  )
fi

if [[ "$user_creation_enabled" == 'true' ]]; then
  printf '%s\n' 'Bootstrap ativado: crie apenas o primeiro administrador em um host controlado.'
  printf '%s\n' 'No Linux, instale e configure o cliente separadamente: ./deploy/install-cli.sh'
  printf '%s\n' 'Em seguida, altere USER_CREATION_ENABLED para false no .env e recrie somente o Hub.'
else
  printf '%s\n' 'Bootstrap desativado. Abra USER_CREATION_ENABLED apenas durante a criação controlada do primeiro administrador.'
fi

case "$publication_mode" in
  local-viewer)
    if [[ "$viewer_bind_address" == '127.0.0.1' ]]; then
      viewer_access_host='localhost'
    else
      viewer_access_host="$hub_dns"
    fi
    printf '\nVisualizador local configurado em https://%s:9091.\n' "$viewer_access_host"
    printf '%s\n' 'O acesso exige username e token pessoal; admin e senior podem criar revisões imutáveis via Hub.'
    printf '%s\n' 'Senior fica limitado ao próprio domínio; o volume do portal permanece somente leitura.'
    ;;
  github)
    printf '\nGitHub Pages selecionado: use o workflow hospedado .github/workflows/deploy.yml.\n'
    printf '%s\n' 'Em Settings > Pages, selecione GitHub Actions; nenhum runner próprio é necessário.'
    ;;
  gitea-compact)
    printf '\nModo Gitea compacto configurado na porta local TCP/9092.\n'
    printf '%s\n' 'O builder fixo não usa Docker socket nem executa workflows do repositório.'
    printf '%s\n' 'Para acesso remoto, mantenha o bind local e use um proxy HTTPS autenticado.'
    ;;
  gitea-runner)
    printf '\nApós criar o admin e fechar o bootstrap, configure o modo avançado em hosts separados:\n'
    printf '  Host dedicado do runner: %s\n' './deploy/install-hub.sh --configure-gitea-runner'
    printf '  Host administrativo:     %s\n' './deploy/install-hub.sh --prepare-nginx-deploy'
    ;;
esac
