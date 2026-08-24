# Implantação isolada: CLI, API e TLS

Este guia separa os artefatos necessários para executar somente o Lucien CLI ou
somente o Runbook API Hub. Os dois componentes se comunicam exclusivamente por
`API_HOST` e TLS; não há endereço embutido no binário.

Para um procedimento guiado completo, incluindo respostas do instalador,
bootstrap e referência dos comandos do CLI, consulte o
[Manual de instalação](manual-instalacao.md).

!!! warning "Certificados não nascem no `up`"
    `docker compose up` não gera certificados. Gere-os explicitamente uma vez
    antes de iniciar o Hub. O projeto oferece o serviço `certgen` para isso;
    os comandos OpenSSL equivalentes estão neste guia para operação sem Docker.

## Executar somente o CLI nativo

Este é o caminho oficial para a captura por PTY: execute o binário no terminal
Linux ou macOS do operador. Um CLI dentro de contêiner gravaria o terminal do
próprio contêiner, e não a sessão real do usuário. Windows não possui suporte a
PTY neste projeto.

| Artefato | Necessário | Finalidade |
| --- | --- | --- |
| binário `lucien` | sim | executável usado no terminal do operador |
| `deploy/install-cli.sh` | recomendado no Linux | detecta arquitetura, instala binário/CA, completion e persiste o ambiente público |
| diretório `cli/` | não | código-fonte usado somente no ambiente central de build |
| variáveis de ambiente do sistema | sim | definem `API_HOST`, `TLS_CA_FILE` e `EDITOR` |
| `certs/ca.crt` | sim | valida o certificado TLS do Hub |
| `certs/ca.key`, `server.key`, `server.crt` | não | nunca devem ser copiados para o cliente |

`docker-compose.yml`, `docs/`, `backend/`, `secret-scanner/`, `certgen/`, PostgreSQL
e SLM não são necessários nesse nó. O arquivo `.env.client.example` é referência
de valores: o binário Go não lê arquivos `.env` sozinho; injete as variáveis pelo
shell, systemd, MDM ou cofre corporativo.

O [tutorial de uso](tutorial.md) mostra como obter o pacote Linux/macOS pré-compilado,
verificar seu checksum e instalá-lo em `~/.local/bin`. Depois da instalação,
configure o terminal do operador:

No Linux, o caminho preferencial é o instalador exclusivo do cliente:

```sh
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

Ele pede o arquivo `.tar.gz`, o checksum correspondente, a URL HTTPS do Hub e o
caminho de uma cópia de `ca.crt`. A CA não é criada no cliente: ela deve ser a CA
pública que assinou o certificado do Hub. O script instala o autocompletar para
o shell de login Bash, Zsh ou Fish e mostra ao final os caminhos do binário, CA,
arquivo de ambiente, completion e perfil do shell.

```sh
export API_HOST="https://runbook.exemplo.interno:8443"
export TLS_CA_FILE="/etc/lucien/ca.crt"
export EDITOR="vi"

lucien login
lucien start redis-cache -d "Validar replicação"
```

O nome em `API_HOST` deve aparecer no SAN do certificado do servidor. O CLI
recusa HTTP e valida a CA indicada por `TLS_CA_FILE`.

O Compose não executa o CLI. Docker é reservado ao Hub e seus serviços de apoio.

Para um jump server automático, transfira adicionalmente
`deploy/install-jump-server.sh` e todo o diretório `deploy/jump/`. Esses arquivos
não são necessários em uma estação pessoal ou WSL; nesses ambientes o fluxo
continua sendo `lucien login` com a credencial individual.

## Executar Hub/API e SLM na mesma máquina

Este é o cenário recomendado quando a instalação Gitea já existe em outra
máquina. Use o perfil `consolidated`: ele inicia PostgreSQL, Hub, Secret Scanner,
Ollama/SLM e o inicializador do modelo no mesmo host. O Gitea continua externo e
é acessado exclusivamente pela API REST configurada em `GIT_API_BASE`.

| Artefato | Necessário | Finalidade |
| --- | --- | --- |
| `docker-compose.yml` | sim | orquestra Hub, PostgreSQL, scanner e SLM |
| diretório `backend/` | sim | imagem da API Hub |
| diretório `secret-scanner/` | sim | Gitleaks obrigatório em modo *enforce* |
| `.env` derivado de `.env.example` | sim | banco, autenticação, SLM local, Gitea e SANs |
| `certs/server.key`, `certs/server.crt`, `certs/ca.crt` | sim | TLS do Hub e healthcheck local |
| diretório `certgen/` | somente na emissão/rotação | gera a CA e o certificado do Hub |
| `deploy/install-hub.sh` | instalação guiada e modos auxiliares | cria `.env`/Compose ou configura runner/SSH em hosts separados |
| `deploy/systemd/act-runner.service` | somente Gitea Actions | unidade endurecida instalada no host dedicado do runner |
| `runbook-viewer/` e `logo-lucien.png` | somente `local-viewer` | portal autenticado em HTTPS/9091; volume read-only e revisões via Hub |
| `wiki-builder/` e `deploy/nginx/wiki-compact.conf` | somente `gitea-compact` | builder fixo e servidor estático sem Docker socket |
| `cli/`, `docs/`, `site/`, MkDocs local e workflows | não | não participam da execução do Hub nem dos dois serviços fixos |

### Estrutura completa para copiar ao servidor do Hub

Para construir as imagens no próprio servidor e gerar os certificados nele,
copie esta estrutura, preservando os nomes e a hierarquia:

```text
lucien-hub/
├── docker-compose.yml              # ou docker-compose.local.yml já gerado
├── docker-compose.build.yml        # build local separado do runtime
├── .dockerignore                   # protege o contexto de build
├── .env                            # configuração local; nunca versione
├── backend/                        # contexto de build da API FastAPI
├── secret-scanner/                 # contexto de build do scanner Gitleaks
├── runbook-viewer/                 # somente no preset local-viewer
├── logo-lucien.png                 # somente no preset local-viewer
├── wiki-builder/                   # somente no preset gitea-compact
├── certgen/                        # necessário para emitir/rotacionar TLS
├── certs/
│   ├── ca.crt                      # CA pública usada no healthcheck
│   ├── server.crt                  # certificado do Hub
│   └── server.key                  # chave privada do Hub
├── secrets/                        # diretório 0700; arquivos 0444 para Compose
└── deploy/
    ├── install-hub.sh              # opcional; somente instalação guiada
    └── nginx/
        └── wiki-compact.conf       # somente no preset gitea-compact
```

Regras para reduzir o conjunto com segurança:

- se os certificados já foram gerados em ambiente controlado, `certgen/` não é
  necessário em execução; copie somente os três arquivos TLS mostrados acima;
- se o `.env` e o Compose já estão prontos, `deploy/install-hub.sh` não é
  necessário no servidor;
- no perfil `consolidated`, não existe outro diretório local para a SLM: o Docker
  baixa a imagem do Ollama e mantém os dados no volume nomeado `ollama-data`;
- com `STORAGE_PROVIDER=local`, os runbooks ficam no volume nomeado
  `playbooks-data`; o portal monta esse volume como `:ro` e o diretório `site/`
  não é destino da API;
- com GitHub ou Gitea, o Hub publica pela API REST do provedor. Não é necessário
  copiar o repositório da wiki nem o diretório `site/` para o servidor do Hub. No
  modo compacto, o builder mantém clone/cache em volumes Docker próprios.

Não copie para o host exclusivo da API: `cli/`, `docs/`, `site/`, `scripts/`,
`.github/`, `.gitea/`, `mkdocs.yml` ou `requirements-docs.txt`. `runbook-viewer/`,
`logo-lucien.png`, `wiki-builder/` e `deploy/nginx/wiki-compact.conf` também podem ser
omitidos quando seus respectivos presets não forem usados. A chave
`certs/ca.key`, criada durante a emissão, não é necessária para executar o Hub e
deve ser removida para um cofre offline após a emissão.

Se não usou o instalador guiado, crie o arquivo antes de editá-lo:

```powershell
Copy-Item .env.example .env
```

Configure os valores não sensíveis no `.env`. As credenciais devem ser arquivos
sem quebra de linha em `secrets/`, com diretório `0700` e arquivos `0444`. O
fluxo manual é mais sujeito a erro; prefira `deploy/install-hub.sh`.

```dotenv
COMPOSE_PROFILES=consolidated
HUB_BIND_ADDRESS=0.0.0.0
SLM_BASE_URL=http://slm:11434
SLM_MODEL=qwen2.5-coder:3b
SLM_LANGUAGE_RUNBOOK=pt-br

STORAGE_PROVIDER=gitea
GIT_API_BASE=https://gitea.exemplo.interno/api/v1
GIT_OWNER=infraestrutura
GIT_REPO=runbooks
GIT_BRANCH=main
GIT_DOCS_PREFIX=docs/runbooks
```

Depois gere o TLS e suba o ambiente:

```powershell
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  --profile tools build certgen
docker compose --profile tools run --rm certgen
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  --profile consolidated build
docker compose --profile consolidated up -d
docker compose --profile consolidated logs -f slm-init
```

Não publique PostgreSQL, SLM ou Secret Scanner. Exponha apenas TCP 8443 do Hub
para as origens autorizadas. O Compose monta `secrets/` em `/run/secrets`, o que
retira os valores do `docker inspect`, mas não protege contra root ou acesso ao
socket Docker. Use Vault/KMS quando o modelo de ameaça exigir essa separação.

O perfil `server` permanece disponível somente para uma implantação em que a SLM
roda em outro host privado. Nesse caso, use `.env.server.example` e configure
`SLM_BASE_URL` com o endpoint remoto. Não use esse perfil quando a SLM deve ficar
junto do Hub.

## Instalador guiado do Hub

Em um host Linux com Docker Compose v2 e OpenSSL, execute o instalador a partir
da raiz do pacote isolado do Hub. Esse pacote ainda precisa conter o
`docker-compose.yml` na raiz, pois ele é o modelo usado para gerar o Compose
local:

```text
lucien-hub/
├── docker-compose.yml
├── docker-compose.build.yml
├── .dockerignore
├── backend/
├── certgen/
├── secret-scanner/
├── runbook-viewer/
├── wiki-builder/
├── logo-lucien.png
├── certs/
└── deploy/
    ├── install-hub.sh
    └── nginx/
        └── wiki-compact.conf
```

Se você já copiou os diretórios e recebeu erro de artefato ausente, copie os três
arquivos da mesma release para a raiz do pacote:

```sh
cp /caminho/do/projeto_lucien/docker-compose.yml ./docker-compose.yml
cp /caminho/do/projeto_lucien/docker-compose.build.yml ./docker-compose.build.yml
cp /caminho/do/projeto_lucien/.dockerignore ./.dockerignore
```

Depois execute:

```sh
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

Ele pergunta pelo FQDN do Hub, exposição de TCP/8443, SLM local ou remota, um dos
quatro presets (`local-viewer`, `github`, `gitea-compact`, `gitea-runner`) e a
abertura controlada do bootstrap. Ao final cria:

| Arquivo | Finalidade |
| --- | --- |
| `.env` | configuração não sensível e tag das imagens, com permissão `0600` |
| `docker-compose.local.yml` | cópia editável da estrutura base do Compose |
| `secrets/` | arquivos `0444`, protegidos no host pelo diretório `0700` |

O script emite os certificados automaticamente quando `ca.crt`, `server.crt` e
`server.key` estão ausentes; se o conjunto estiver completo, ele o reutiliza sem
rotação. Um conjunto parcial interrompe a instalação. O script pede confirmação
para iniciar os serviços e se recusa a sobrescrever `.env` ou
`docker-compose.local.yml`. Não instala Docker, não altera o CLI e não mostra os
segredos gerados no terminal.

Use o arquivo local de Compose para operação posterior:

```sh
docker compose --env-file .env -f docker-compose.local.yml \
  -f docker-compose.build.yml build
docker compose --env-file .env -f docker-compose.local.yml up -d
docker compose --env-file .env -f docker-compose.local.yml logs -f hub
```

Para GitHub ou Gitea, informe no diálogo um token de publicação com escopo mínimo
para alterar somente o repositório de runbooks. O token fica em
`secrets/git_token`, montado somente no Hub.

## Gerar certificados com a ferramenta do projeto

Este é o caminho preferencial: o script usa RSA 4096 bits para a CA, RSA 3072 bits
para o servidor, SANs explícitos e permissões restritivas.

1. Antes de gerar, defina em `.env` os nomes realmente usados pelos clientes:

   ```dotenv
   CERT_DNS=runbook.exemplo.interno,hub,localhost
   CERT_IP=127.0.0.1
   CERTS_DIR=./certs
   ```

2. Gere uma única vez:

   ```powershell
   docker compose --profile tools run --rm certgen
   ```

3. Distribua somente `certs/ca.crt` aos clientes. Mantenha `certs/ca.key` fora do
   host de aplicação sempre que possível; `certs/server.key` fica apenas no Hub.

O gerador recusa sobrescrever chaves existentes. Para rotacionar, gere um novo
conjunto em diretório seguro, troque a CA nos clientes de forma coordenada e só
então altere o certificado do Hub. Não apague certificados em uso como atalho.

Se o Hub aparecer como `unhealthy` com `CA cert does not include key usage
extension`, a CA foi emitida por uma versão antiga do gerador. Faça uma rotação
coordenada: preserve os arquivos atuais como backup, gere um novo conjunto com
o `certgen` atualizado, distribua somente o novo `ca.crt` aos clientes e recrie
o Hub. A CA precisa conter `keyUsage=critical,keyCertSign,cRLSign`; apenas
`openssl verify` não detecta essa omissão em todas as versões do OpenSSL/Python.

## Gerar certificados manualmente com OpenSSL

Use este procedimento quando não puder usar o contêiner `certgen`. Substitua
`runbook.exemplo.interno` pelo FQDN presente em `API_HOST`; inclua todos os nomes
ou IPs que os clientes realmente usam.

```powershell
New-Item -ItemType Directory -Force certs | Out-Null

# CA privada: guarde fora do host do Hub após emitir o certificado.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out certs/ca.key
openssl req -x509 -new -sha256 -days 3650 `
  -key certs/ca.key `
  -subj "/C=BR/O=Lucien/CN=Lucien Internal CA" `
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" `
  -addext "keyUsage=critical,keyCertSign,cRLSign" `
  -addext "subjectKeyIdentifier=hash" `
  -out certs/ca.crt

# Chave e CSR do Hub.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out certs/server.key
openssl req -new -sha256 `
  -key certs/server.key `
  -subj "/C=BR/O=Lucien/CN=runbook-hub" `
  -out certs/server.csr

# Extensões e SANs do certificado de servidor.
@'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:runbook.exemplo.interno,DNS:hub,DNS:localhost,IP:127.0.0.1
'@ | Set-Content -Encoding ascii certs/server.ext

openssl x509 -req -sha256 -days 397 `
  -in certs/server.csr `
  -CA certs/ca.crt `
  -CAkey certs/ca.key `
  -CAcreateserial `
  -extfile certs/server.ext `
  -out certs/server.crt

openssl verify -CAfile certs/ca.crt certs/server.crt
Remove-Item certs/server.csr, certs/server.ext, certs/ca.srl
```

Em host Linux, ajuste o acesso do usuário não privilegiado do contêiner antes de
iniciar o Hub:

```sh
chmod 0600 certs/ca.key certs/server.key
chmod 0644 certs/ca.crt certs/server.crt
chown 10001:10001 certs/server.key certs/server.crt
```

No Docker Desktop, prefira o `certgen`: ele já aplica as permissões esperadas e
evita diferenças de ACL entre Windows e Linux. Nunca envie `ca.key` ou `server.key`
por e-mail, repositório, chat ou para o nó do CLI.
