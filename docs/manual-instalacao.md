# Manual de instalação do Lucien

Este manual descreve uma instalação distribuída com:

- Hub, PostgreSQL, Secret Scanner e SLM Ollama no mesmo servidor Linux;
- Gitea em um servidor externo;
- Lucien CLI executado diretamente no terminal dos operadores;
- publicação dos runbooks em um repositório Gitea.

Os exemplos usam nomes reservados. Substitua `<HOST_DO_HUB>`, `<REDE_AUTORIZADA>`
e os demais valores pelo ambiente real. Não publique IPs, tokens ou nomes internos
na documentação de um repositório público.

!!! danger "Não misture arquivos de versões diferentes"
    `deploy/install-hub.sh`, `docker-compose.yml`, `backend/` e os demais
    diretórios devem vir da mesma release do Lucien. O instalador atual apresenta
    quatro modos de publicação. Se a tela mostrar somente três destinos, atualize
    o pacote completo antes de uma nova instalação; trocar apenas o script pode
    gerar um Compose incompatível.

## 1. Escolher o modo de publicação

O instalador atual oferece:

| Opção | Uso | Requisitos adicionais no host do Hub |
| --- | --- | --- |
| `1) local-viewer` | disco local e portal HTTPS/9091 | `runbook-viewer/` e `logo-lucien.png` |
| `2) github` | GitHub-hosted Actions e GitHub Pages | nenhum runner local |
| `3) gitea-compact` | builder fixo e Nginx no host do Hub | `wiki-builder/` e `deploy/nginx/wiki-compact.conf` |
| `4) gitea-runner` | Gitea Actions em runner dedicado | workflow no repositório e outro host para o runner |

Para Gitea externo com Actions, selecione `4) gitea-runner`. A estrutura mínima
do servidor do Hub é:

```text
lucien-hub/
├── docker-compose.yml
├── docker-compose.build.yml
├── .dockerignore
├── backend/
├── certgen/
├── certs/
├── deploy/
└── secret-scanner/
```

No modo compacto, o serviço one-shot `wiki-volume-init` deve aparecer como
`Exited (0)`. Ele prepara os volumes para o builder não-root; esse estado é
sucesso, assim como ocorre com `slm-init` depois de baixar o modelo.

`docker-compose.local.yml` e `.env` serão criados pelo instalador. O diretório
`certs-invalidos/` não participa da execução e não deve permanecer no servidor:
ele contém chaves privadas antigas e precisa ser arquivado em cofre offline ou
eliminado conforme a política corporativa.

## 2. Pré-requisitos do servidor

Use um host Linux dedicado ou uma VM com:

- Docker Engine e Docker Compose v2;
- OpenSSL e `coreutils`;
- saída HTTPS para baixar imagens, o modelo Ollama e acessar o Gitea;
- espaço persistente para PostgreSQL e Ollama;
- TCP/8443 acessível exclusivamente pelas redes dos clientes.

Confirme os requisitos:

```bash
docker version
docker compose version
openssl version
realpath --version
```

Se o Hub for publicado em um IP de Internet, restrinja TCP/8443 por firewall,
VPN ou rede privada. TLS e API token são obrigatórios, mas não justificam deixar
a porta aberta para `0.0.0.0/0`. PostgreSQL, Ollama e Secret Scanner nunca devem
ter portas publicadas.

## 3. Executar o instalador do Hub

Na raiz de uma cópia completa e coerente do pacote:

```bash
cd /opt/lucien-hub
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

Use esta referência para responder aos prompts:

| Prompt | Resposta recomendada | Explicação |
| --- | --- | --- |
| FQDN usado pelos clientes | `hub.exemplo.interno` | Nome presente em `API_HOST` e no certificado. Prefira DNS a IP. |
| Expor HTTPS em TCP/8443 | `y` somente para CLI remoto | Faz bind em todas as interfaces; o firewall ainda deve limitar as origens. |
| IP adicional do SAN | IP pelo qual o CLI acessará o Hub | Necessário quando o cliente usa diretamente um endereço IP. |
| Executar Ollama na mesma máquina | `y` | Seleciona o perfil `consolidated`. |
| Modelo da SLM | `qwen2.5-coder:3b` ou modelo homologado | O primeiro uso baixa o modelo e pode demorar. |
| Idioma dos runbooks | `pt-br` ou `en` | Define o template entregue pelo Hub ao CLI e o idioma das tags inferidas. |
| Modo de publicação | `4` para Gitea Actions | Use `3` somente para o builder compacto no próprio host. |
| Base da API do Gitea | `https://gitea.exemplo.interno/api/v1` | Use uma única barra antes de `api/v1`. |
| Organização/proprietário | proprietário exato do repositório | O valor pode ser sensível a maiúsculas conforme o provedor. |
| Repositório | nome do repositório de runbooks | Use um repositório dedicado. |
| Branch | `main` | Deve ser a mesma branch observada pelo workflow. |
| Diretório MkDocs | `docs/runbooks` | Mantém os documentos dentro da árvore compilada. |
| Token Git | token de serviço restrito ao repositório | Conceda somente leitura/escrita de conteúdo; nunca privilégios administrativos. |
| Abrir bootstrap | `y` somente na primeira instalação | A janela deve ser fechada logo após criar o primeiro administrador. |
| Subir o Hub | `y` | Constrói e inicia os serviços selecionados. |

O instalador consulta a quantidade de CPUs efetivamente disponibilizada pelo
daemon Docker e não gera limites acima dela. Isso é especialmente importante no
Docker Desktop, cuja VM pode ter menos CPUs que o sistema operacional hospedeiro.

O instalador não pergunta se deve gerar TLS. Quando `ca.crt`, `server.crt` e
`server.key` estão ausentes, ele executa o `certgen` automaticamente. Quando os
três existem, reutiliza o conjunto sem rotacioná-lo. Um conjunto parcial é
rejeitado para impedir que certificados e chaves de emissões diferentes sejam
misturados.

Se não existir DNS e o CLI usar um IP, informe esse IP como endereço do Hub e
repita-o no prompt de SAN adicional. Para instalações permanentes, DNS interno e
certificado emitido para esse nome são preferíveis.

### Branch `master`

O workflow entregue observa `main`. Se o repositório Gitea usa `master`, escolha
uma destas alternativas antes de publicar:

1. migre a branch padrão para `main` e mantenha o workflow entregue; ou
2. configure `GIT_BRANCH=master` e altere também o gatilho do workflow:

```yaml
on:
  push:
    branches:
      - master
```

Configurar o Hub para `master` e deixar o workflow em `main` faz o arquivo chegar
ao repositório, mas não dispara a compilação da wiki.

## 4. Conferir os arquivos gerados

O instalador cria:

```text
.env                         # configuração não sensível; modo 0600
docker-compose.local.yml     # Compose operacional editável
secrets/                     # segredos server-side; diretório 0700
├── postgres_password        # arquivos individuais no modo 0444
├── database_url
├── bootstrap_api_key
├── auth_pepper
├── git_token
├── viewer_session_secret
└── wiki_repository_token
certs/ca.crt                 # CA pública distribuída aos clientes
certs/ca.key                 # chave privada da CA; retirar do host após backup
certs/server.crt             # certificado do Hub
certs/server.key             # chave privada usada somente pelo Hub
```

Corrija qualquer URL com barra duplicada. Por exemplo:

```dotenv
GIT_API_BASE=https://gitea.exemplo.interno/api/v1
```

Confira apenas variáveis não secretas:

```bash
grep -E '^(COMPOSE_PROFILES|API_HOST|HUB_BIND_ADDRESS|SLM_BASE_URL|SLM_MODEL|SLM_LANGUAGE_RUNBOOK|STORAGE_PROVIDER|GIT_API_BASE|GIT_OWNER|GIT_REPO|GIT_BRANCH|GIT_DOCS_PREFIX)=' .env
```

O `.env` não contém mais credenciais. Ainda assim, não despeje a configuração em
logs públicos: URLs e nomes internos também podem ser sensíveis. Nunca execute
`cat secrets/*`; acesso root ou ao socket Docker continua equivalendo a acesso aos
segredos montados.

### Ajustar uma instalação já criada

!!! warning "Não troque apenas o Compose de uma instalação legada"
    Se `.env` ainda contém `POSTGRES_PASSWORD`, `DATABASE_URL`,
    `BOOTSTRAP_API_KEY`, `AUTH_PEPPER` ou tokens, ele pertence ao contrato antigo.
    O Compose novo espera arquivos em `secrets/` e imagens já construídas com
    `LUCIEN_IMAGE_TAG`. Faça uma migração controlada ou, quando não há dados a
    preservar, uma instalação limpa com o pacote completo. Misturar os dois
    formatos impede PostgreSQL e Hub de iniciarem.

Se `.env`, `docker-compose.local.yml` e os certificados válidos já existem, não
execute o instalador novamente: ele se recusa a sobrescrever esses arquivos.
Abra `.env` com um editor administrativo e:

1. troque `https://gitea.exemplo.interno//api/v1` por
   `https://gitea.exemplo.interno/api/v1`;
2. decida entre `main` e `master` e use o mesmo valor em `GIT_BRANCH` e no
   workflow Gitea;
3. preserve `secrets/` e seus modos `0700/0444`, salvo se houver rotação planejada.

Ao atualizar para o upload assíncrono, copie o novo `backend/`,
`docker-compose.yml` e `docker-compose.build.yml`. O arquivo ativo é
`docker-compose.local.yml`. Depois de copiar os arquivos novos, sincronize-o com
a base atual:

```bash
./deploy/install-hub.sh --refresh-compose
```

O comando valida que a base contém `upload-worker` e que todos os serviços têm
limites e reservas de CPU e memória. Se já houver um Compose local diferente,
ele será preservado como `docker-compose.local.yml.bak.*` antes da substituição.
Revise e reaplique ao arquivo novo apenas customizações realmente necessárias;
prefira manter ajustes operacionais no `.env`. Sem `upload-worker`, Jobs ficam
indefinidamente em `PROCESSING`.

No `.env`, adicione ou ajuste:

```dotenv
SLM_LANGUAGE_RUNBOOK=pt-br
SLM_TIMEOUT_SECONDS=300
UPLOAD_WORKER_POLL_SECONDS=2
UPLOAD_WORKER_LEASE_SECONDS=900
UPLOAD_WORKER_RETRY_BASE_SECONDS=10
UPLOAD_WORKER_MAX_ATTEMPTS=5
```

`SLM_LANGUAGE_RUNBOOK` aceita somente `pt-br` ou `en`. A mudança afeta novos
rascunhos abertos pelo CLI, tags, objetivo, arquitetura/pré-requisitos, impactos
e rollback sugeridos pela SLM; não traduz documentos já publicados nem o texto
escrito manualmente pelo operador.

`UPLOAD_WORKER_LEASE_SECONDS` deve ser ao menos duas vezes o timeout da SLM mais
30 segundos. Defina uma nova tag imutável em `LUCIEN_IMAGE_TAG`. Essa tag é
compartilhada por todas as imagens construídas localmente; portanto, construa
todas as imagens dos perfis ativos, e não somente o Hub:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  -f docker-compose.build.yml build
```

O `upload-worker` reutiliza a imagem do Hub e não possui build separado. Se
somente `hub` for construído após a troca da tag, o Compose tentará baixar
`secret-scanner`, `wiki-builder` ou outro serviço local de um registry e falhará
com `pull access denied`.

Ao atualizar uma instalação que já possui banco, copie também
`backend/migrations/007_command_outputs_postgresql.sql`, pare os consumidores e
aplique a migração uma vez:

```bash
docker compose --env-file .env -f docker-compose.local.yml stop hub upload-worker
docker compose --env-file .env -f docker-compose.local.yml exec -T postgres \
  sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backend/migrations/007_command_outputs_postgresql.sql
```

Depois recrie API e worker com a mesma imagem versionada:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  up -d --force-recreate hub upload-worker
```

Em banco novo, `create_all()` já cria as colunas e a migração `007` não é necessária.
Não gere certificados novamente por causa dessa correção. O certificado atual
continua válido enquanto SAN, validade e chave privada estiverem corretos.

Proteja os arquivos:

```bash
chmod 0600 .env certs/ca.key certs/server.key
chmod 0444 secrets/*
chmod 0700 secrets
chmod 0644 certs/ca.crt certs/server.crt
chown 10001:10001 certs/server.key certs/server.crt
```

Depois de armazenar `ca.key` em dois meios offline controlados e testar a leitura
do backup, retire a cópia do host do Hub. Não remova a única cópia: ela é
necessária para uma reemissão controlada pela mesma CA.

## 5. Validar o Hub

Confira os contêineres e os logs da API:

```bash
docker compose --env-file .env -f docker-compose.local.yml ps
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 hub
```

Valide o certificado e o endpoint sem desabilitar TLS:

```bash
openssl x509 -in certs/server.crt -noout -subject -issuer -ext subjectAltName

curl --fail --show-error \
  --cacert certs/ca.crt \
  https://<HOST_DO_HUB>:8443/health
```

Resultado esperado:

```json
{"status":"ok"}
```

Não use `curl -k`. Se a validação falhar, corrija CA, SAN, relógio ou hostname;
desabilitar a verificação esconderia o problema.

## 6. Preparar a máquina do CLI

O CLI é um binário nativo. Docker não é necessário na máquina do operador.
Transfira por canal confiável somente:

- `deploy/install-cli.sh`;
- `deploy/install-jump-user.sh`, somente para o modo manual por conta;
- `deploy/install-jump-server.sh` e `deploy/jump/`, somente para o modo
  automático do jump server;
- `lucien_<versao>_linux_<amd64|arm64>.tar.gz`;
- o arquivo `.tar.gz.sha256` correspondente;
- `ca.crt` público copiado do Hub.

Nunca transfira `.env`, `ca.key`, `server.key` ou o token Git.

Execute:

```bash
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

O instalador valida checksum, arquitetura e extensões da CA. Depois solicita:

| Campo | Exemplo |
| --- | --- |
| Escopo | usuário atual (`~/.local/bin`) ou sistema (`/usr/local/bin`) |
| Pacote | `/tmp/lucien_1.2.3_linux_amd64.tar.gz` |
| CA pública | `/tmp/ca.crt` |
| URL do Hub | `https://<HOST_DO_HUB>:8443` |
| Editor | `vi`, `vim` ou outro editor confiável |

O script pode validar `/health` e criar o primeiro administrador. A chave de
bootstrap é lida sem eco e usada somente em memória. Ela não é salva no arquivo
de ambiente do CLI. O autocompletar é instalado automaticamente para o shell de
login Bash, Zsh ou Fish. Em instalações de sistema, os três formatos são
instalados nos diretórios convencionais de `/usr/local/share`.

Para carregar a configuração imediatamente, execute o comando exibido pelo
instalador. Em uma instalação por usuário, normalmente será:

```bash
. "$HOME/.config/lucien/env"
lucien help
```

## 7. Criar o primeiro administrador

Se essa opção foi aceita no instalador do CLI, informe um username e a
`BOOTSTRAP_API_KEY` armazenada no servidor em `secrets/bootstrap_api_key`. Leia-a
somente em terminal administrativo não gravado e transfira-a ao operador por um
canal seguro ou cofre temporário. Não passe a chave como argumento, não a coloque
em chat e não a deixe no ambiente permanente.

O procedimento manual equivalente é:

```bash
read -r -s -p 'Bootstrap key: ' LUCIEN_BOOTSTRAP_KEY
printf '\n'
export LUCIEN_BOOTSTRAP_KEY
lucien create user administrador
unset LUCIEN_BOOTSTRAP_KEY
```

`lucien create user` cria exclusivamente o primeiro administrador. Ele não é um
comando de cadastro geral. Usuários posteriores são criados pelo administrador
através da API IAM do Hub.

No sucesso, a credencial permanente é exibida uma única vez e salva para o mesmo
usuário do sistema operacional. Guarde-a em cofre antes de limpar o terminal e
valide imediatamente:

```bash
lucien auth status
```

Não execute o bootstrap com `sudo` se a conta comum usará o CLI; o perfil e o
cofre pertencem à conta que executa o comando.

Depois do sucesso, feche imediatamente a janela no servidor:

```bash
sed -i 's/^USER_CREATION_ENABLED=.*/USER_CREATION_ENABLED=false/' .env
chmod 0600 .env
docker compose --env-file .env -f docker-compose.local.yml \
  up -d --force-recreate hub
```

Valide novamente `/health`. O banco também impede um segundo bootstrap, mas a
flag desativada reduz a superfície exposta e mantém a intenção operacional clara.

## 8. Referência dos comandos do CLI

### Formato do `API_HOST`

O CLI aceita apenas a origem do Hub — esquema, host e porta:

```
API_HOST=https://lucien-api.interno:8443
```

Credencial embutida, caminho, query ou fragmento são recusados na
inicialização. Não é preciosismo: o valor prefixa toda chamada e compõe o nome
da conta no keyring. Um `usuario:senha@` gravaria credencial no nome da entrada
e vazaria em qualquer lugar que registre o endereço; um caminho deslocaria
silenciosamente cada endpoint, e o erro apareceria como `404` do Hub em vez de
configuração inválida.

A barra final é aceita: `https://hub:8443` e `https://hub:8443/` descrevem a
mesma origem.

### `lucien --version`

Mostra a versão gravada no binário durante o empacotamento:

```
lucien version 1.1.5
```

Um binário compilado localmente, sem passar pelo `scripts/build-cli.sh`, mostra
`dev` — o que já distingue build de desenvolvimento de pacote publicado. É a
primeira coisa a conferir quando o comportamento de uma máquina diverge de
outra.

### `lucien help`

Mostra os comandos disponíveis. Use `lucien <comando> --help` para consultar
argumentos e flags específicas.

### `lucien create user <nome>`

Cria o primeiro administrador pelo bootstrap e ativa seu perfil local. Exige a
janela `USER_CREATION_ENABLED=true` no Hub e `LUCIEN_BOOTSTRAP_KEY` somente nessa
execução. Não use para usuários comuns.

### `lucien login`

Solicita uma credencial sem eco. Uma credencial provisória é trocada uma única
vez por outra permanente; uma permanente é validada em `/me`. O resultado é
guardado no keyring do usuário do sistema operacional:

```bash
lucien login
```

Se a resposta da troca se perder, o CLI repete uma vez com a mesma chave
idempotente; o Hub devolve a mesma permanente, sem criar outra credencial.

O CLI nunca define o próprio papel ou domínio; esses dados sempre vêm do Hub.

`login` instala uma credencial nova; ele não mostra a sessão atual. Para validar
a credencial já salva, use `lucien auth status`.

### Cadastrar e administrar os demais usuários

!!! warning "As flags de `admin user` mudaram de nome"
    `--role` passou a significar **área** (a mesma coisa que o `-r` do
    `lucien start`), e aceita uma lista. O nível de permissão, que antes usava
    `--role`, agora é `--level`. `--domain` deixou de existir: use `-r`.

    Um script antigo com `--role senior` não faz nada silenciosamente errado —
    o Hub recusa com `área 'senior' não existe`, porque `senior` não está em
    `RUNBOOK_DOMAIN_FUNCTIONS`.

    ```bash
    lucien admin user create joao --level senior -r servidores,acessos
    ```


Depois de autenticado como admin:

```bash
lucien admin user create operador.rede \
  --role junior \
  --domain redes
```

A credencial provisória aparece uma única vez, expira em quatro horas e permite
uma única troca. Transfira-a por cofre corporativo ou canal seguro aprovado; não
a envie em chat, e-mail ou sessão gravada. Na máquina do usuário:

```bash
lucien login
# Cole o token no prompt sem eco.
lucien auth status
```

Operações administrativas adicionais aceitam UUID ou username:

```bash
lucien admin user update operador.rede --role pleno --domain redes
lucien admin user issue-provisional-token operador.rede
lucien admin user revoke operador.rede --yes
```

A nova provisória invalida imediatamente as credenciais anteriores. A revogação
exige `--yes` para evitar execução acidental.

Se a criação do usuário terminar com resultado de rede incerto, não crie outro
username. Execute `issue-provisional-token` para o mesmo username: se a criação
tiver sido confirmada no Hub, uma nova provisória substitui com segurança a que
não foi entregue; se não tiver sido, o Hub responde `404`.

### Recuperar um administrador sem token válido

Não reabra o bootstrap e não apague o banco. No host do Hub, depois de instalar
esta versão atualizada:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec hub python -m app.recover_admin Admin
```

No cliente do administrador:

```bash
lucien login
# Cole o token recém-exibido.
lucien auth status
```

A recuperação é uma operação local privilegiada do Hub. Ela emite uma
credencial provisória por quatro horas, não expõe rota de recuperação na rede e
registra o evento sem incluir a credencial.

### Usar em jump server

Cada operador precisa de uma conta Unix individual fornecida pelo SSSD. Depois
de instalar o CLI e a CA, emita no host do Hub uma credencial técnica exclusiva:

```bash
docker compose --env-file .env \
  -f docker-compose.local.yml \
  exec -T hub python -m app.issue_jump_enrollment_key
```

O valor `luc_jump_...` é exibido uma vez. Transfira-o por canal administrativo
seguro e não o cole em tickets, histórico de shell ou arquivos do usuário. No
jump server, com o repositório completo e o CLI já instalado, execute:

```bash
chmod +x deploy/install-jump-server.sh
sudo ./deploy/install-jump-server.sh
```

Informe a URL HTTPS do Hub, a CA pública, a conta local administrativa
(`valdemir`), o username correspondente no Hub (`Admin`) e a credencial M2M. O
instalador grava a credencial em
`/etc/lucien/secrets/jump_enrollment_key` (`root:root`, `0600`), instala o helper
restrito por sudoers, o banner, o hook em `/etc/profile.d` e valida TLS e SSH.

No primeiro login LDAP, o helper consulta o Hub pelo mesmo ID POSIX. Se a
identidade não existir, pergunta uma única vez:

1. Acessos (`acessos`);
2. Servidores (`servidores`);
3. Network (`redes`);
4. Suporte (`suporte`).

O papel inicial de um usuário novo é sempre `pleno`, abaixo de `senior`.
Identidades existentes `junior`, `pleno` ou `senior` preservam integralmente
papel e domínio; contas `admin` usam o fluxo administrativo separado. O token
provisório é trocado pelo CLI via `stdin` e o permanente fica no keyring ou no fallback
`0600` da própria conta. Em logins seguintes, `/me` é validado silenciosamente.
A conta local `valdemir` usa a identidade `Admin` já configurada; se o cofre
estiver vazio, seu token será solicitado sem eco.

O modo jump não altera instalações comuns do CLI. Fora desse host, não defina
`LUCIEN_JUMP_MODE` nem `LUCIEN_EXPECTED_USERNAME`: o operador continua fazendo
`lucien login` com seu token pessoal e usa todos os comandos sem LDAP ou
credencial M2M.

Restrinja no firewall do Hub o acesso a `/auth/jump/enroll` à origem do jump
server sempre que a topologia permitir. Para rotacionar a credencial M2M,
execute novamente o módulo no Hub e depois o instalador no jump server; a
credencial anterior deixa de funcionar imediatamente.

Não use contas Unix compartilhadas. Keyring, perfil, rascunhos e arquivos de
fallback pertencem à conta local; compartilhar essa conta elimina o isolamento
entre operadores.

### `lucien start <nome_do_projeto> [-d "descrição"]`

Abre um PTY e começa a gravar a sessão localmente. O nome identifica a tarefa ou
projeto; ele não escolhe GitHub, Gitea ou o provider de storage. `-d` ou
`--describe` aceita até 280 caracteres e é opcional, mas recomendado para
melhorar a extração da SLM.

```bash
lucien start manutencao-redis \
  -d "Diagnosticar replicação e latência do Redis"
```

O PTY é criado com o tamanho do seu terminal e acompanha o redimensionamento por
`SIGWINCH`. Sem terminal de origem — execução por pipe ou agendador — assume
80x24. Isso importa para sessões SSH abertas dentro da gravação: o cliente SSH
propaga as dimensões locais ao equipamento remoto, e uma OLT, CMTS ou roteador
que recebe zero linhas não desenha nada, deixando a sessão aparentemente
congelada.

Sessões SSH para equipamentos de rede são gravadas como qualquer outra: os
comandos digitados no CLI do equipamento e suas saídas entram no log da mesma
forma, sem configuração adicional.

### O que a descrição do `-d` vira no documento

O texto de `lucien start -d` aparece como subtítulo da seção `## Objetivo`:

```markdown
## Objetivo

### Comandos para verificação de rota down nas OLT's ZTE

> **REVISÃO OBRIGATÓRIA — DESCRIÇÃO DO OPERADOR:** texto informado na captura;
> complete o objetivo antes da publicação.
```

Substitua o texto da citação pelo objetivo em si; o subtítulo já identifica o
assunto e é o que aparece no índice da wiki.

### Publicar em outra função de domínio

`lucien start <nome> -r <funcao>` escolhe o diretório de destino da publicação
ainda na captura:

```bash
lucien start exemplo -r acessos -d "minha publicacao em outra role"
```

O artefato vai para `<ano>/acessos/` em vez do domínio do autor. Sem `-r`, o
destino continua sendo o seu próprio domínio — o comportamento de sempre.

Duas regras valem aqui, e as duas são do Hub, não do CLI:

A função precisa existir em `RUNBOOK_DOMAIN_FUNCTIONS`. Se não existir, o upload
é recusado e a mensagem lista as disponíveis. O `lucien start` só valida a
gramática (minúsculas, 3 a 64 caracteres) porque grava offline e não conhece a
configuração do Hub.

O domínio é escopo de autoridade, não preferência: um `senior` de `servidores`
que pedir `-r acessos` recebe `403`. Somente `admin` publica fora do próprio
domínio. A restrição de criticidade alta para `junior` continua valendo por
cima disso, sem alteração.

Uma nota de vocabulário, porque `-r` usa a palavra "role" num sentido específico.
Aqui **role é a área** — `acessos`, `servidores`, `roteamento` — e é ela que vira
diretório. É o que o código chama de `domain_function`.

Não confunda com o **nível de permissão** (`junior`, `pleno`, `senior`, `admin`),
que o código chama de `role_level`. Esse continua fixo no código, porque cada
nível carrega regra própria de RBAC.

E nenhum dos dois é o **cargo** da pessoa. O Lucien não modela cargo: se um
coordenador é `senior` ou `junior` aqui é decisão da sua organização, e pode não
ter relação nenhuma com o título dele.

### Trabalhar em mais de uma área

Se você atende mais de uma área, o admin concede as duas de uma vez:

```bash
lucien admin user update U000004 -r servidores,acessos
```

A primeira é a primária — o destino sem `-r`. Confira o que você tem:

```bash
lucien auth status
```

```
Authenticated as U000004 (11111111-1111-4111-8111-111111111111); level=senior areas=servidores, acessos.
```

### `lucien stop`

Encerra a captura e preserva o log localmente. Não envia nada ao Hub. Essa
separação permite parar a gravação mesmo quando a rede ou a API estão
indisponíveis. Execute `lucien stop` diretamente dentro do shell gravado; o
comando encerra o PTY e devolve o terminal original. Também é possível executá-lo
em um segundo terminal da mesma conta.

Ao encerrar, o terminal recebe o lembrete do próximo passo:

```
Session olt-rota-down-20260819-233443-2f7add630842 stopped and preserved locally.
Next: lucien upload
After acceptance, upload will return the Job_ID and status command.
```

Quem imprime é o processo do `lucien start`, depois de restaurar o terminal.
Isso vale também quando você simplesmente sai do shell gravado com `exit`, sem
usar `lucien stop`: a sessão fica preservada do mesmo jeito e o lembrete aparece,
porque nada foi enviado ao Hub ainda e o log continua esperando o `upload`.

### `lucien upload [-s|--skip-enrichment]`

Sanitiza escapes ANSI e envia a última sessão encerrada ao Hub. O aceite retorna
rapidamente com `Job_ID` e `PROCESSING`; a SLM roda no `upload-worker`. O CLI só
remove os arquivos locais após o `202 Accepted`. Em falha anterior ao aceite,
preserva a sessão e reconcilia pelo nome antes de criar outro Job.

`--skip-enrichment` dispensa a segunda chamada à SLM para este Job. A extração de
comandos continua acontecendo; o rascunho sai com a estrutura básica e sem
objetivo, impactos ou rollback sugeridos. O opt-out é do operador e prevalece
mesmo com `SLM_ENRICHMENT_ENABLED=true` no Hub. Use em hosts onde a inferência é
lenta demais para caber em `SLM_TIMEOUT_SECONDS`.

### `lucien job status <id_ou_nome_ou_indice>`

Mostra `PROCESSING`, `PENDING`, `FAILED` ou `PUBLISHED`. Em `FAILED`, mostra um
código seguro de diagnóstico, nunca conteúdo do log.

### `lucien job retry <id_ou_nome_ou_indice> [-s|--skip-enrichment]`

Reenfileira um Job `FAILED` próprio. O payload sanitizado permanece cifrado no
PostgreSQL até o processamento concluir, o proprietário apagá-lo ou reenfileirá-lo.

Sem o flag, o retry preserva a escolha feita no `upload` original. Com
`--skip-enrichment`, o reprocessamento passa a dispensar o enriquecimento — o
caminho indicado quando o Job falhou com `UPSTREAM_ERROR` por timeout da SLM.

### `lucien reviews`

Lista a fila do usuário autenticado com ID, nome, status e data. Inclui Jobs
`PROCESSING`, `PENDING` e `FAILED`; publicações concluídas não aparecem.
Para acompanhamento contínuo no Linux, use `watch -n 5 lucien reviews`.

Todos os comandos de Job (`job`, `status`, `retry`, `sent` e `del`) aceitam a
posição de base 1 exibida por `lucien reviews`, além do UUID ou nome. Como a fila
pode mudar, consulte `reviews` imediatamente antes de usar um índice em
publicação ou exclusão.

### `lucien job <id_ou_nome_ou_indice>`

Baixa os comandos detectados, abre uma seleção interativa e inicia o editor
configurado em `EDITOR`. Ao fechar o editor, salva um rascunho local com permissão
restrita. O comando ainda não publica o documento. Um número positivo referencia
a posição de base 1 mostrada por `lucien reviews`; IDs e nomes continuam aceitos.

### `lucien job cat <id_do_job>`

Imprime o rascunho salvo, sem abrir o editor. Leitura pura: não altera nada.

Serve para diagnosticar uma recusa. Quando o Hub bloqueia a publicação, a
mensagem nomeia a regra; este comando entrega o texto para você procurar a
linha. A saída vai para `stdout`, então aceita pipe:

```bash
lucien job cat f51201f2-388a-4ce5-99ea-5d59f9424ca9 | grep -n -i 'senha\|password'
```

O conteúdo vem do **rascunho local**, e o comando **nunca fala com o Hub** — um
rascunho recusado nunca chegou lá, e um diagnóstico que dependesse do Hub
falharia justamente quando há o que diagnosticar.

Por isso ele exige o ID exato, e não aceita índice nem nome: resolver esses
precisaria da lista do Hub. `lucien runbook revise` cobra o UUID pelo mesmo tipo
de razão. Pegue o ID em `lucien reviews` ou na saída do `lucien job`.

O comando **se recusa a rodar dentro de uma sessão gravada**. Ele despeja o
rascunho no terminal, e ali isso entraria na própria captura — inclusive o
segredo que motivou a recusa. Rode de outro terminal.

Não usa paginador. Para paginar, use pipe: `lucien job cat <id> | less`.

### `lucien job sent <id_ou_nome_ou_indice>`

Envia o rascunho ao Hub com chave idempotente derivada do usuário, Job e conteúdo.
O Hub executa secret scanning, DLP, validação Markdown, RBAC e injeção de
frontmatter. O rascunho local só é removido depois da confirmação de publicação.

### `lucien job del <id_ou_nome_ou_indice> [-y]`

Exclui um Job próprio `PENDING` ou `FAILED`. Para cancelar e expurgar um Job
preso em `PROCESSING`, use `lucien job del <id_ou_nome_ou_indice> --force`; a remoção da
fila é transacional. `--yes` pula somente a confirmação interativa. Nenhuma
combinação de flags apaga um Job `PUBLISHED`.

### `lucien runbook revise <uuid_do_runbook_publicado>`

Corrige um runbook já publicado. Baixa o corpo do Hub, abre o `EDITOR` e devolve o
resultado; o Hub cria uma **nova versão imutável** com outro UUID, preserva a
anterior e registra a linhagem (`runbook_raiz`, `revisao`, `substitui`). Vale nos
três provedores — `local`, `github` e `gitea` — com as mesmas regras.

Exige o UUID exato da publicação. Diferente dos comandos de Job, `revise` **não**
aceita índice de `lucien reviews` nem nome: um erro de um dígito no índice
publicaria a correção sobre o runbook errado, e a fila muda entre um comando e o
outro. Pegue o UUID no portal, na URL do artefato publicado ou na saída do
`lucien job sent` que o originou.

```sh
lucien runbook revise 3e381ebe-0284-4d3b-b304-a13655e3dd4c
```

Somente `admin` e `senior` revisam; o `senior` fica restrito ao domínio imutável da
publicação raiz. Fora dele a resposta é `404`, e não `403`, para não confirmar a
existência do runbook. Com `RBAC_ENTRY_ROLES_ENABLED=true`, junior e pleno também
revisam dentro do próprio domínio.

O corpo baixado vem **sem** frontmatter: ele é gerado pelo Hub e recusado se vier do
cliente. Preencha `ultimo_revisor` e `data_revisao` pelo fluxo de revisão do
repositório, não colando frontmatter no editor. Fechar o editor sem alterar nada
cancela a operação sem consumir um novo UUID. Um `412` significa que outra revisão
foi publicada enquanto a sua estava aberta: rode o comando de novo para partir da
versão atual.

## 9. Primeiro fluxo completo

Depois de autenticar:

```bash
lucien start manutencao-redis \
  -d "Validar replicação do Redis após manutenção"
```

Execute os comandos da manutenção no shell aberto pelo Lucien. No próprio shell
gravado, encerre e preserve a sessão:

```bash
lucien stop
```

De volta ao terminal original, prossiga:

```bash
lucien upload
lucien job status <JOB_ID>
lucien reviews
lucien job <JOB_ID>
lucien job sent <JOB_ID>
```

Como alternativa, `exit` encerra o shell gravado e já deixa a sessão como
`STOPPED`; nesse caso, siga diretamente para `lucien upload`. Não é necessário
executar `exit` antes de `lucien stop`.
Nunca grave comandos que imprimam tokens, senhas ou chaves; DLP e secret scanning
são barreiras de contenção, não um cofre.

## 10. Próxima etapa do Gitea Actions

Somente depois de criar o administrador e fechar o bootstrap:

1. confirme que `.gitea/workflows/deploy.yml` observa a mesma branch de
   `GIT_BRANCH`;
2. habilite Actions no Gitea e no repositório;
3. registre o runner no host dedicado:

   ```bash
   ./deploy/install-hub.sh --configure-gitea-runner
   ```

4. prepare a chave SSH e o acesso ao Nginx no host administrativo:

   ```bash
   ./deploy/install-hub.sh --prepare-nginx-deploy
   ```

Esses modos não devem ser executados dentro da cópia operacional do Hub: o
instalador recusa a configuração do runner quando detecta `.env` ou Compose do
Hub no mesmo diretório. Consulte [Publicação da wiki](publicacao.md) para os
segredos do workflow, restrições do runner e configuração do Nginx.

## 11. Checklist final

- [ ] `/health` responde com a CA correta e sem `-k`.
- [ ] TCP/8443 aceita somente redes autorizadas.
- [ ] PostgreSQL, SLM e scanner não estão publicados.
- [ ] URL do Gitea não contém `//api/v1`.
- [ ] `GIT_BRANCH` e o workflow observam a mesma branch.
- [ ] Token Git possui escopo mínimo e pertence a uma identidade de serviço.
- [ ] Primeiro administrador foi criado e `USER_CREATION_ENABLED=false` aplicado.
- [ ] Apenas `ca.crt` foi distribuído aos clientes.
- [ ] `ca.key` e chaves antigas foram retiradas do host após backup validado.
- [ ] CLI executa nativamente e consegue repetir `upload` após falha de rede.
