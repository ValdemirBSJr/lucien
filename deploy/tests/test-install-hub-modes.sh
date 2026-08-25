#!/usr/bin/env bash
set -euo pipefail

# Exercita o instalador em cópias descartáveis. Nenhum daemon Docker é acessado.
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT

falhar() {
  printf 'Falha: %s\n' "$1" >&2
  exit 1
}

assert_linha() {
  local arquivo="$1"
  local linha="$2"
  grep -Fqx -- "$linha" "$arquivo" || \
    falhar "linha ausente em $arquivo: $linha"
}

assert_regex() {
  local arquivo="$1"
  local regex="$2"
  grep -Eq -- "$regex" "$arquivo" || \
    falhar "padrão ausente em $arquivo: $regex"
}

preparar_pacote() {
  local nome="$1"
  local estado_certificados="${2:-completos}"
  local raiz="$TEMP_ROOT/$nome"

  mkdir -p "$raiz/deploy/tests" "$raiz/backend" "$raiz/certgen" \
    "$raiz/secret-scanner" "$raiz/runbook-viewer" "$raiz/wiki-builder" \
    "$raiz/deploy/nginx" "$raiz/certs" "$raiz/fakebin"
  cp "$PROJECT_ROOT/deploy/install-hub.sh" "$raiz/deploy/install-hub.sh"
  cp "$PROJECT_ROOT/docker-compose.yml" "$raiz/docker-compose.yml"
  cp "$PROJECT_ROOT/docker-compose.build.yml" "$raiz/docker-compose.build.yml"
  cp "$PROJECT_ROOT/.dockerignore" "$raiz/.dockerignore"
  touch "$raiz/backend/Dockerfile" "$raiz/certgen/Dockerfile" \
    "$raiz/secret-scanner/Dockerfile" "$raiz/runbook-viewer/Dockerfile" \
    "$raiz/wiki-builder/Dockerfile" "$raiz/deploy/nginx/wiki-compact.conf" \
    "$raiz/logo-lucien.png"

  case "$estado_certificados" in
    completos)
      touch "$raiz/certs/ca.crt" "$raiz/certs/server.crt" \
        "$raiz/certs/server.key"
      ;;
    ausentes) ;;
    parciais) touch "$raiz/certs/ca.crt" ;;
    *) falhar "estado de certificados inválido: $estado_certificados" ;;
  esac

  # O stub aceita apenas a sondagem de versão; qualquer execução real falha o teste.
  cat > "$raiz/fakebin/docker" <<'EOF'
#!/usr/bin/env sh
if [ "$#" -eq 2 ] && [ "$1" = 'compose' ] && [ "$2" = 'version' ]; then
  exit 0
fi
if [ "$#" -eq 3 ] && [ "$1" = 'info' ] && [ "$2" = '--format' ]; then
  printf '%s\n' '2'
  exit 0
fi
printf '%s\n' "$*" >> "$FAKE_PROJECT_ROOT/docker-calls.txt"
ultimo_argumento=''
for argumento do
  ultimo_argumento="$argumento"
done
if [ "$ultimo_argumento" = 'certgen' ]; then
  touch "$FAKE_PROJECT_ROOT/certs/ca.crt" \
    "$FAKE_PROJECT_ROOT/certs/server.crt" \
    "$FAKE_PROJECT_ROOT/certs/server.key"
  exit 0
fi
printf 'Docker real não deve ser chamado neste teste: %s\n' "$*" >&2
exit 99
EOF
  cat > "$raiz/fakebin/openssl" <<'EOF'
#!/usr/bin/env sh
if [ "$#" -eq 3 ] && [ "$1" = 'rand' ] && [ "$2" = '-hex' ] && [ "$3" = '32' ]; then
  printf '%s\n' 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
  exit 0
fi
printf 'Uso inesperado do stub OpenSSL\n' >&2
exit 99
EOF
  chmod +x "$raiz/deploy/install-hub.sh" "$raiz/fakebin/docker" \
    "$raiz/fakebin/openssl"
  printf '%s' "$raiz"
}

executar_instalador() {
  local nome="$1"
  local respostas="$2"
  local estado_certificados="${3:-completos}"
  local raiz
  raiz="$(preparar_pacote "$nome" "$estado_certificados")"

  (
    cd "$raiz"
    printf '%s' "$respostas" | \
      FAKE_PROJECT_ROOT="$raiz" PATH="$raiz/fakebin:/usr/bin:/bin" \
        ./deploy/install-hub.sh \
        > "$raiz/saida.txt" 2>&1
  )
  [[ "$(stat -c '%a' "$raiz/.env")" == '600' ]] || \
    falhar '.env não foi criado com modo 0600'
  [[ "$(stat -c '%a' "$raiz/secrets")" == '700' ]] || \
    falhar 'diretório secrets não foi criado com modo 0700'
  local secret_file
  for secret_file in "$raiz"/secrets/*; do
    [[ "$(stat -c '%a' "$secret_file")" == '444' ]] || \
      falhar "segredo não foi criado com modo 0444: $secret_file"
  done
  ! grep -Eq '^(POSTGRES_PASSWORD|DATABASE_URL|BOOTSTRAP_API_KEY|AUTH_PEPPER|GIT_TOKEN|VIEWER_SESSION_SECRET|WIKI_REPOSITORY_TOKEN)=' "$raiz/.env" || \
    falhar 'um segredo foi persistido no .env'
  assert_linha "$raiz/.env" 'SECRETS_DIR=./secrets'
  assert_regex "$raiz/.env" '^LUCIEN_IMAGE_TAG=src-[a-f0-9]{16}$'
  assert_linha "$raiz/.env" 'LUCIEN_TINY_CPU_LIMIT=0.50'
  assert_linha "$raiz/.env" 'LUCIEN_SMALL_CPU_LIMIT=1.00'
  assert_linha "$raiz/.env" 'LUCIEN_MEDIUM_CPU_LIMIT=2.00'
  assert_linha "$raiz/.env" 'LUCIEN_SLM_CPU_LIMIT=2.00'
  printf '%s' "$raiz"
}

testar_certificados_tls() {
  local raiz raiz_parcial

  raiz="$(executar_instalador tls-ausentes $'\n\n\ny\n\n\n1\n\n\n\n' ausentes)"
  [[ -f "$raiz/certs/ca.crt" && -f "$raiz/certs/server.crt" && \
    -f "$raiz/certs/server.key" ]] || \
    falhar 'instalação limpa não gerou o conjunto TLS'
  grep -Fq -- 'certgen' "$raiz/docker-calls.txt" || \
    falhar 'certgen não foi invocado quando os certificados estavam ausentes'
  grep -Fq -- 'TLS certificates missing; generating' "$raiz/saida.txt" || \
    falhar 'geração automática de TLS não foi informada'

  raiz="$(executar_instalador tls-existentes $'\n\n\ny\n\n\n1\n\n\n\n' completos)"
  [[ ! -e "$raiz/docker-calls.txt" ]] || \
    falhar 'certificados existentes acionaram o Docker indevidamente'
  grep -Fq -- 'Existing TLS certificates detected' "$raiz/saida.txt" || \
    falhar 'reutilização de TLS não foi informada'

  raiz_parcial="$(preparar_pacote tls-parciais parciais)"
  if (
    cd "$raiz_parcial"
    printf '%s' $'\n\n\ny\n\n\n1\n\n\n\n' | \
      FAKE_PROJECT_ROOT="$raiz_parcial" \
      PATH="$raiz_parcial/fakebin:/usr/bin:/bin" ./deploy/install-hub.sh \
        > "$raiz_parcial/saida.txt" 2>&1
  ); then
    falhar 'conjunto TLS parcial foi aceito'
  fi
  grep -Fq -- 'incomplete TLS set' "$raiz_parcial/saida.txt" || \
    falhar 'erro de conjunto TLS parcial não foi informado'
}

testar_local_viewer() {
  local raiz hash_antes hash_depois
  raiz="$(executar_instalador local-viewer $'\n\n\ny\n\n\n1\n\n\n\n\n')"

  assert_linha "$raiz/.env" 'COMPOSE_PROFILES=consolidated,local-viewer'
  assert_linha "$raiz/.env" 'SLM_LANGUAGE_RUNBOOK=pt-br'
  assert_linha "$raiz/.env" 'STORAGE_PROVIDER=local'
  assert_linha "$raiz/.env" 'VIEWER_BIND_ADDRESS=127.0.0.1'
  assert_regex "$raiz/secrets/viewer_session_secret" '^[a-f0-9]{64}$'
  [[ ! -s "$raiz/secrets/wiki_repository_token" ]] || \
    falhar 'token do builder deveria estar vazio no modo local'
  grep -Fq -- 'https://localhost:9091' "$raiz/saida.txt" || \
    falhar 'URL do visualizador não foi informada'

  # Uma segunda execução deve falhar sem alterar a configuração já emitida.
  hash_antes="$(sha256sum "$raiz/.env" | awk '{print $1}')"
  if (
    cd "$raiz"
    PATH="$raiz/fakebin:/usr/bin:/bin" ./deploy/install-hub.sh \
      > "$raiz/segunda-saida.txt" 2>&1
  ); then
    falhar 'o instalador sobrescreveu uma configuração existente'
  fi
  hash_depois="$(sha256sum "$raiz/.env" | awk '{print $1}')"
  [[ "$hash_antes" == "$hash_depois" ]] || \
    falhar 'a configuração existente foi alterada na segunda execução'
}

testar_github() {
  local raiz token='token_publicacao_github'
  raiz="$(executar_instalador github \
    $'\n\n\ny\n\nen\n2\ny\n\n\n\n\ntoken_publicacao_github\n\n\n\n')"

  assert_linha "$raiz/.env" 'COMPOSE_PROFILES=consolidated'
  assert_linha "$raiz/.env" 'SLM_LANGUAGE_RUNBOOK=en'
  assert_linha "$raiz/.env" 'STORAGE_PROVIDER=github'
  assert_linha "$raiz/secrets/git_token" "$token"
  # A chave de sessão do portal é local e aleatória: é gerada em todos os
  # presets. Deixá-la vazia criava um secret de zero byte que só falhava depois,
  # no boot do runbook-viewer, com uma mensagem que não apontava a instalação.
  assert_regex "$raiz/secrets/viewer_session_secret" '^[a-f0-9]{64}$'
  [[ ! -s "$raiz/secrets/wiki_repository_token" ]] || \
    falhar 'token do builder deveria estar vazio no modo GitHub'
  grep -Fq -- 'no self-hosted runner is needed' "$raiz/saida.txt" || \
    falhar 'orientação do GitHub-hosted runner ausente'
  ! grep -Fq -- "$token" "$raiz/saida.txt" || falhar 'token GitHub vazou na saída'
}

testar_gitea_compact() {
  local raiz publish_token='token_publicacao_gitea' read_token='token_leitura_builder'
  raiz="$(executar_instalador gitea-compact \
    $'\n\n\ny\n\n\n3\n\n\n\n\n\ntoken_publicacao_gitea\n\n\n\ntoken_leitura_builder\n\n\n\n')"

  assert_linha "$raiz/.env" 'COMPOSE_PROFILES=consolidated,gitea-compact'
  assert_linha "$raiz/.env" 'STORAGE_PROVIDER=gitea'
  assert_linha "$raiz/.env" 'GIT_CA_SOURCE=./certs/ca.crt'
  assert_linha "$raiz/.env" \
    'WIKI_REPOSITORY_URL=https://gitea.example.internal/infrastructure/runbooks.git'
  assert_linha "$raiz/.env" 'WIKI_REPOSITORY_BRANCH=main'
  assert_linha "$raiz/.env" 'WIKI_REPOSITORY_USER=lucien-builder'
  assert_linha "$raiz/secrets/wiki_repository_token" "$read_token"
  assert_linha "$raiz/.env" 'WIKI_BIND_ADDRESS=127.0.0.1'
  assert_regex "$raiz/docker-compose.local.yml" '^  wiki-volume-init:$'
  assert_regex "$raiz/docker-compose.local.yml" '^    cap_add: \["CHOWN", "FOWNER"\]$'
  [[ "$(grep -Fc -- 'condition: service_completed_successfully' \
    "$raiz/docker-compose.local.yml")" == '3' ]] || \
    falhar 'builder, Nginx e SLM não aguardam a preparação dos volumes'
  ! grep -Fq -- "$publish_token" "$raiz/saida.txt" || \
    falhar 'token de publicação Gitea vazou na saída'
  ! grep -Fq -- "$read_token" "$raiz/saida.txt" || \
    falhar 'token do builder vazou na saída'
}

testar_gitea_runner() {
  local raiz token='token_publicacao_runner'
  raiz="$(executar_instalador gitea-runner \
    $'\n\n\ny\n\n\n4\n\n\n\n\n\ntoken_publicacao_runner\n\n\n\n\n')"

  assert_linha "$raiz/.env" 'COMPOSE_PROFILES=consolidated'
  assert_linha "$raiz/.env" 'STORAGE_PROVIDER=gitea'
  [[ ! -s "$raiz/secrets/wiki_repository_token" ]] || \
    falhar 'token do builder deveria estar vazio no modo runner'
  grep -Fq -- '--configure-gitea-runner' "$raiz/saida.txt" || \
    falhar 'orientação da VM dedicada do runner ausente'
  ! grep -Fq -- "$token" "$raiz/saida.txt" || falhar 'token do runner vazou na saída'
}

testar_atualizacao_compose() {
  local raiz backup quantidade_backups
  raiz="$(preparar_pacote refresh-compose completos)"
  printf '%s\n' 'services:' '  postgres:' > "$raiz/docker-compose.local.yml"

  (
    cd "$raiz"
    PATH="$raiz/fakebin:/usr/bin:/bin" \
      ./deploy/install-hub.sh --refresh-compose > "$raiz/saida.txt" 2>&1
  )

  cmp -s "$raiz/docker-compose.yml" "$raiz/docker-compose.local.yml" || \
    falhar 'a atualização não sincronizou o Compose local com a base'
  backup="$(find "$raiz" -maxdepth 1 -name 'docker-compose.local.yml.bak.*' -print -quit)"
  [[ -n "$backup" ]] || falhar 'a atualização não preservou o Compose anterior'
  assert_linha "$backup" '  postgres:'
  assert_linha "$raiz/docker-compose.local.yml" '  upload-worker:'
  grep -Fq -- 'resource limits for every service' "$raiz/saida.txt" || \
    falhar 'o resultado da atualização não foi informado'

  (
    cd "$raiz"
    PATH="$raiz/fakebin:/usr/bin:/bin" \
      ./deploy/install-hub.sh --refresh-compose > "$raiz/segunda-saida.txt" 2>&1
  )
  quantidade_backups="$(find "$raiz" -maxdepth 1 \
    -name 'docker-compose.local.yml.bak.*' | wc -l)"
  [[ "$quantidade_backups" == '1' ]] || \
    falhar 'a atualização idempotente criou um backup desnecessário'
  grep -Fq -- 'is already up to date' "$raiz/segunda-saida.txt" || \
    falhar 'a atualização idempotente não informou o estado atual'
}

testar_secrets_sem_zero_byte() {
  # Um secret de zero byte passa despercebido na instalação e só quebra no boot
  # do serviço que o consome. A chave de sessão do portal é gerada localmente,
  # então nunca deve sair vazia, em nenhum preset.
  local raiz preset entrada

  for preset in local-viewer github gitea-compact; do
    case "$preset" in
      local-viewer) entrada=$'


y


1



' ;;
      github)       entrada=$'


y

en
2
y




token_publicacao_github



' ;;
      gitea-compact) continue ;;
    esac
    raiz="$(executar_instalador "zero-byte-$preset" "$entrada")"
    assert_regex "$raiz/secrets/viewer_session_secret" '^[a-f0-9]{64}$'
    [[ -s "$raiz/secrets/viewer_session_secret" ]] ||       falhar "viewer_session_secret ficou vazio no preset $preset"
  done

  # Secrets que dependem de valor externo podem ficar vazios, mas a instalação
  # precisa dizer isso em vez de deixar a descoberta para o runtime.
  raiz="$(executar_instalador aviso-vazio $'


y


1



')"
  grep -Fq -- 'came out empty' "$raiz/saida.txt" ||     falhar 'instalação não avisou sobre secret criado vazio'
}

testar_hardening_compose() {
  local compose="$PROJECT_ROOT/docker-compose.yml"
  local sem_limite

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
    falhar "serviços sem limites e reservas: ${sem_limite//$'\n'/, }"
  ! grep -Fq -- '    build:' "$compose" || \
    falhar 'o Compose de runtime não pode reconstruir imagens'
  assert_linha "$compose" '      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password'
  assert_linha "$compose" '      DATABASE_URL_FILE: /run/secrets/database_url'
  assert_linha "$compose" '      GIT_TOKEN_FILE: /run/secrets/git_token'
  assert_linha "$compose" '      WIKI_REPOSITORY_TOKEN_FILE: /run/secrets/wiki_repository_token'
  assert_linha "$compose" '  upload-worker:'
  assert_linha "$compose" '    command: ["python", "-m", "app.worker"]'
  ! grep -Eq -- '^[[:space:]]+(POSTGRES_PASSWORD|DATABASE_URL|BOOTSTRAP_API_KEY|AUTH_PEPPER|GIT_TOKEN|VIEWER_SESSION_SECRET|WIKI_REPOSITORY_TOKEN):' "$compose" || \
    falhar 'o Compose de runtime ainda injeta um segredo diretamente'
  assert_linha "$compose" '    user: "10003:10003"'
  [[ "$(grep -Fc -- 'ollama/ollama:0.31.2@sha256:' "$compose")" == '3' ]] || \
    falhar 'as imagens Ollama não estão fixadas por digest'
}

bash -n "$PROJECT_ROOT/deploy/install-hub.sh"
testar_certificados_tls
testar_local_viewer
testar_github
testar_gitea_compact
testar_gitea_runner
testar_atualizacao_compose
testar_hardening_compose
testar_secrets_sem_zero_byte
printf '%s\n' 'install-hub: instalação, atualização e quatro modos validados sem acessar o Docker real.'
