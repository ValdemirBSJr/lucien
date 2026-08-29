# Tutorial de uso

Este tutorial executa o ciclo completo: iniciar o ambiente, criar a identidade
inicial, gravar uma sessão, revisar os comandos e publicar o runbook.

## Pré-requisitos

- Docker Engine com Compose v2;
- portas locais necessárias disponíveis;
- um editor de terminal, como `vi`, `vim` ou `nano`;
- acesso ao repositório do projeto;
- CLI nativo para Linux ou macOS; Windows não é suportado para captura PTY;
- Docker Desktop integrado ao Ubuntu/WSL no ambiente central de build;
- para ambiente distribuído, DNS e firewall liberando cliente → Hub/TCP 8443.

!!! danger "Antes de começar"
    Nunca use os valores `CHANGE_ME` em um ambiente real. Não coloque tokens,
    senhas ou chaves na descrição da tarefa, nos comandos ou no Markdown.

## 1. Preparar o ambiente consolidado

Na raiz do pacote Linux, use o instalador. Ele gera configuração, secrets com
permissões restritas, tag de imagem derivada do conteúdo e certificados:

```bash
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

O `.env` contém apenas opções não sensíveis. Senha do PostgreSQL, URL do banco,
bootstrap, pepper e tokens ficam em arquivos `0444` sob o diretório `secrets/`
com modo `0700`. A chave de
bootstrap usada pelo CLI deve ser lida de `secrets/bootstrap_api_key` e transferida
por canal seguro somente para a execução controlada que cria o primeiro admin.

Para o primeiro uso, mantenha:

```dotenv
COMPOSE_PROFILES=consolidated
# Use localhost quando o CLI nativo estiver na mesma máquina do Hub.
API_HOST=https://localhost:8443
USER_CREATION_ENABLED=true
STORAGE_PROVIDER=local
```

`API_HOST` é o único vínculo de endereço entre CLI e Hub. Não codifique hostname
ou IP no cliente.

## 2. Validar os certificados TLS

```bash
openssl verify -CAfile certs/ca.crt certs/server.crt
```

O instalador já gera o conjunto quando ele está ausente e rejeita conjuntos
parciais. O certificado precisa conter todos os nomes usados em `API_HOST`.
Distribua apenas `ca.crt` aos clientes; a chave privada da CA não deve ser montada
no Hub nem copiada para o CLI.

## 3. Iniciar os serviços

Se respondeu “não” ao prompt **Subir o Hub agora**, execute:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.build.yml build
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml logs -f slm-init
```

Aguarde o download e a preparação do modelo. Verifique o Hub:

```bash
docker compose ps
```

O PostgreSQL e o Ollama devem permanecer em redes privadas, sem portas expostas
publicamente.

## 4. Distribuir e instalar o CLI nativo

O CLI deve ser executado diretamente no terminal Linux ou macOS do operador para
que o PTY grave a sessão real. O contêiner `lucien` não é o fluxo de captura.

O operador não deve compilar o cliente. Em uma máquina central de build, gere os
pacotes Linux e macOS para `amd64` e `arm64`, com seus checksums. O compilador Go
roda dentro do Docker:

```sh
chmod +x scripts/build-cli.sh
VERSION=1.2.3 ./scripts/build-cli.sh # substitua pela versão aprovada
```

O resultado fica em `dist/` e pode ser publicado no repositório de artefatos da
empresa. Não versione esses binários no repositório de código. Cada pacote contém
apenas `lucien` e instruções curtas; tokens e certificados continuam separados.
Os pacotes macOS são cross-compilados, mas não assinados nem notarizados. Para uma
distribuição corporativa sem alertas do Gatekeeper, assine com Developer ID e
notarize em um runner macOS protegido. Windows continua sem suporte à captura PTY.

No Linux, copie estes quatro artefatos para o host do operador ou para o host
administrativo usado no bootstrap:

- `deploy/install-cli.sh`;
- `lucien_<versao>_linux_<arquitetura>.tar.gz`;
- o arquivo `.tar.gz.sha256` correspondente;
- somente o `ca.crt` público gerado no Hub.

Execute o instalador separado do Hub:

```sh
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

O instalador detecta `amd64`/`arm64`, pede o caminho do pacote e de `ca.crt`,
valida checksum, arquitetura e extensões da CA, e então oferece dois escopos:

- usuário atual: `~/.local/bin/lucien`, CA em `~/.local/share/lucien/` e ambiente
  em `~/.config/lucien/env`;
- sistema: `/usr/local/bin/lucien`, CA em `/etc/lucien/` e ambiente em
  `/etc/profile.d/lucien.sh`, usando `sudo` ou execução como `root`.

Ele também solicita `API_HOST` e `EDITOR`, pode testar `/health` e, opcionalmente,
executar o bootstrap do primeiro administrador. `LUCIEN_BOOTSTRAP_KEY` é lida sem
eco e usada somente nessa execução; ela não entra no arquivo de ambiente. O
autocompletar é instalado automaticamente para Bash, Zsh ou Fish; abra um novo
terminal para carregá-lo.

!!! warning "A CA pertence ao Hub"
    O instalador do CLI não cria certificados. Gere a CA e o certificado no Hub,
    depois copie somente `certs/ca.crt`. Uma CA criada no cliente não assinaria o
    certificado do Hub e, portanto, não permitiria a conexão TLS.

Para instalar manualmente no Linux ou no macOS, detecte o pacote correspondente,
valide-o e instale o binário:

```sh
VERSION=1.2.3 # substitua pela versão aprovada recebida
case "$(uname -s)" in Linux) sistema=linux ;; Darwin) sistema=darwin ;; *) exit 1 ;; esac
case "$(uname -m)" in x86_64) arquitetura=amd64 ;; arm64|aarch64) arquitetura=arm64 ;; *) exit 1 ;; esac
pacote="lucien_${VERSION}_${sistema}_${arquitetura}.tar.gz"

# Receba o pacote, seu arquivo .sha256 e a CA pública por canal confiável.
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c "$pacote.sha256"
else
  shasum -a 256 -c "$pacote.sha256"
fi
tar -xzf "$pacote"
install -d -m 0755 "$HOME/.local/bin" "$HOME/.local/share/lucien"
install -m 0755 "lucien_${VERSION}_${sistema}_${arquitetura}/lucien" "$HOME/.local/bin/lucien"
install -m 0644 ca.crt "$HOME/.local/share/lucien/ca.crt"

# Disponibiliza o comando neste terminal.
export PATH="$HOME/.local/bin:$PATH"
export API_HOST="https://localhost:8443"
export TLS_CA_FILE="$HOME/.local/share/lucien/ca.crt"
export EDITOR="vi"
command -v lucien
lucien help
```

Em ambiente distribuído, substitua `localhost` pelo FQDN do Hub e instale somente
`ca.crt` em um caminho protegido do cliente. O binário não carrega `.env` sozinho:
o mecanismo de instalação deve injetar essas variáveis no ambiente.

Para manter o `PATH` em novos terminais, use `~/.profile` no Linux/bash ou
`~/.zprofile` no macOS/zsh:

```sh
perfil="$HOME/.profile"
[ "$(uname -s)" = "Darwin" ] && perfil="$HOME/.zprofile"
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$perfil" 2>/dev/null || \
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$perfil"
```

Em uma instalação corporativa gerenciada, a equipe de plataforma pode instalar
`lucien` em `/usr/local/bin` e a CA em `/etc/lucien/ca.crt`. O operador não deve
usar `sudo` para criar uma instalação própria. Nunca copie `ca.key`, `server.key`
ou `server.crt` para o cliente.

## 5. Criar o primeiro administrador

Execute o bootstrap somente no host controlado que possui a chave. Ela não deve
entrar no ambiente permanente dos operadores:

Se você escolheu criar o administrador dentro de `deploy/install-cli.sh`, pule o
comando manual abaixo e siga diretamente para o fechamento do bootstrap. O CLI já
salvou o nome do usuário no perfil local e o token no keyring ou, se autorizado,
em arquivo com permissão `0600`. A credencial permanente também é exibida uma vez
para armazenamento no cofre administrativo.

O CLI é nativo e pode rodar diretamente no WSL, sem entrar no Compose. Para um
teste no mesmo WSL do Hub, use o IP real do servidor e copie somente `ca.crt` para
o ambiente do CLI. Não faça `source .env`: esse arquivo contém segredos do Hub.

```sh
export API_HOST=https://10.0.0.1:8443
export TLS_CA_FILE=/caminho/seguro/ca.crt
```

Troque `10.0.0.1` pelo IP informado no instalador. Se o CLI estiver em outra
máquina, copie apenas `certs/ca.crt`; nunca copie `ca.key`, `server.key` ou o
`.env` do Hub.

```sh
read -r -s -p "LUCIEN_BOOTSTRAP_KEY: " LUCIEN_BOOTSTRAP_KEY; echo
export LUCIEN_BOOTSTRAP_KEY
lucien create user administrador
unset LUCIEN_BOOTSTRAP_KEY
```

O Hub recusa novo bootstrap após existir um administrador, mesmo se alguém retiver
a variável. Essa garantia é transacional no banco e vale entre workers e réplicas;
o bootstrap não reabre após revogação. Ainda assim, retenção de segredo no cliente
é uma falha operacional e não deve ser aceita.

Depois do sucesso:

1. defina `USER_CREATION_ENABLED=false`;
2. mantenha `BOOTSTRAP_API_KEY` exclusivamente em secret server-side;
3. recrie somente o Hub para aplicar o fechamento da janela.

```bash
docker compose --profile consolidated up -d --force-recreate hub
```

No WSL, usando o Compose local criado pelo instalador, o equivalente é:

```sh
sed -i 's/^USER_CREATION_ENABLED=.*/USER_CREATION_ENABLED=false/' .env
docker compose --env-file .env -f docker-compose.local.yml \
  --profile consolidated up -d --force-recreate hub
```

Se você escolheu o perfil `server`, substitua `consolidated` por `server`.

Somente depois dessa etapa prossiga para Gitea Actions. O `act_runner` com acesso
ao Docker equivale a `root`; em produção ele deve ficar em host dedicado, separado
do Hub, banco, SLM e Gitea. A instalação guiada do Hub rejeita o modo runner quando
detecta configuração do Hub no mesmo diretório.

O comando `create user` não é um cadastro geral: ele cria exclusivamente o
primeiro admin. Usuários posteriores são criados por um administrador através de
`POST /admin/users`.

## 6. Fazer login

Se o instalador ou `lucien create user` acabou de criar o primeiro administrador,
não execute `login`: o token já foi salvo automaticamente. Confirme com
`lucien auth status`.

Para cadastrar os demais usuários, o administrador executa:

```bash
lucien admin user create operador \
  --role junior \
  --domain servidores
```

A credencial provisória é mostrada uma única vez, expira em quatro horas e deve
ser entregue por cofre/canal seguro. O usuário instala o CLI e a CA pública,
executa `lucien login` e cola a credencial no prompt sem eco. O Hub a consome,
emite a permanente e o CLI a exibe uma vez e a salva localmente. Confirme com
`lucien auth status`.

Se o último administrador perder sua credencial, recupere-a no host do Hub sem
reabrir o bootstrap nem apagar dados:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec hub python -m app.recover_admin Admin
```

Use sempre o prompt sem eco; o CLI rejeita token como argumento para evitar
histórico do shell e exposição na lista de processos:

```bash
lucien login
```

O CLI valida a credencial permanente em `/me` e a guarda no keyring disponível.
Em host Unix controlado, o fallback em arquivo exige
`LUCIEN_ALLOW_FILE_TOKEN=true` e usa permissão `0600`.

Se a credencial permanente for perdida, o admin executa:

```bash
lucien admin user issue-provisional-token operador
```

A nova provisória invalida imediatamente a permanente anterior e qualquer
provisória pendente. O usuário repete `lucien login` dentro de quatro horas.

### Jump server

O administrador instala a integração uma única vez no host. Primeiro, emite a
credencial de escopo mínimo no Hub:

```bash
docker compose --env-file .env \
  -f docker-compose.local.yml \
  exec -T hub python -m app.issue_jump_enrollment_key

sudo ./deploy/install-jump-server.sh
```

O segundo comando pede a credencial `luc_jump_...` sem eco. Em cada login SSH
interativo, o ID LDAP é comparado ao username do Hub. Usuário novo escolhe sua
área e é criado como `pleno`; usuários existentes `junior`, `pleno` ou `senior`
preservam papel e domínio. Contas `admin` usam o login administrativo. Tokens
passam por `stdin`, nunca por argumentos ou `.bashrc`. Se a autenticação falhar,
as operações protegidas do CLI ficam bloqueadas, mas `lucien stop` continua
disponível para preservar uma sessão local.

Não use esse modo com uma conta Unix compartilhada. O procedimento administrativo
completo está em [Manual de instalação](manual-instalacao.md#usar-em-jump-server).

## 7. Gravar uma tarefa

No primeiro terminal, inicie a captura:

```bash
lucien start redis-cache -d "Atualizar Redis e validar replicação"
```

`-d` ou `--describe` é opcional, aceita até 280 caracteres e é recomendado para
melhorar a desambiguação da SLM. A descrição é sanitizada e não concede qualquer
privilégio.

Execute normalmente os comandos da manutenção dentro do shell aberto. Não cole
credenciais. No próprio shell gravado, encerre o PTY e preserve a sessão local:

```bash
lucien stop
```

O comando termina o shell filho e devolve o terminal original; não execute
`exit` antes dele. Se preferir, `stop` também pode ser chamado em um segundo
terminal da mesma conta. Digitar apenas `exit` é uma alternativa válida e deixa
a sessão pronta para `upload`. Como ainda não houve comunicação com o Hub, `stop` não pode
exibir um `Job_ID`; sua saída orienta executar `lucien upload`, que retorna o ID e
o comando de acompanhamento depois do aceite.

`stop` não consulta token nem Hub. Para sanitizar e enviar a sessão encerrada:

```bash
lucien upload
```

O retorno de `upload` contém o `Job_ID` e `Status: PROCESSING`; ele não espera a
SLM. Consulte o andamento com:

```bash
lucien job status <JOB_ID>
```

Em `PENDING`, continue com `reviews` e edição. Em `FAILED`, corrija a saúde da SLM
ou do scanner e execute `lucien job retry <JOB_ID>`. Se autenticação, rede ou Hub
falharem antes do aceite, estado e log permanecem locais; repita somente `upload`.
O CLI e o Hub recusam `retry` enquanto o Job está `PROCESSING`. Isso não bloqueia
o upload de outra sessão: cada nova captura aceita cria seu próprio Job na fila.
O CLI reconcilia uma resposta perdida pelo nome antes de remover os arquivos.
Enquanto houver uma sessão aguardando upload, um novo `start` é recusado para evitar
sobrescrita e perda de auditoria.

## 8. Acompanhar a fila de trabalhos

```bash
lucien reviews
```

A tabela mostra os Jobs ativos pertencentes ao usuário autenticado:

- `PROCESSING`: o worker ainda está processando a sessão;
- `PENDING`: os comandos já podem ser revisados;
- `FAILED`: o processamento falhou e pode ser reenfileirado após o diagnóstico.

Jobs `PUBLISHED` não aparecem nessa fila. Para consultar um Job específico, use
`lucien job status <JOB_ID>`. Outro usuário não consegue consultar, editar,
publicar ou apagar esses Jobs.

Todos os comandos que selecionam um Job (`job`, `status`, `retry`, `sent` e
`del`) também aceitam a posição de base 1 exibida por `lucien reviews`. Como a
fila é dinâmica, execute `reviews` novamente antes de publicar ou excluir por
índice.

Se um processamento precisar ser abandonado, cancele-o explicitamente:

```bash
lucien job del <JOB_ID> --force
```

O Hub remove atomicamente o Job e o payload cifrado da fila. A flag não permite
apagar publicações concluídas e não contorna o isolamento por proprietário.

No Linux, para atualizar a tabela automaticamente a cada cinco segundos:

```bash
watch -n 5 lucien reviews
```

## 9. Revisar e redigir o runbook

```bash
lucien job <id_ou_nome_ou_indice>
```

O índice é a posição de base 1 exibida pelo último `lucien reviews`: `lucien job
1` abre o primeiro Job da tabela e `lucien job 2`, o segundo. A tabela permanece
inalterada e o CLI consulta novamente a mesma lista antes de resolver o índice.
Se a fila mudar entre os comandos, execute `lucien reviews` novamente antes de
usar uma posição.

O CLI apresenta os comandos extraídos. Marque apenas os úteis. Em seguida, ele
abre o editor configurado em `EDITOR` e cria um template com objetivo,
pré-requisitos, procedimento, validação e rollback. O idioma do template vem do
Hub: `SLM_LANGUAGE_RUNBOOK=pt-br` gera todo o esqueleto e solicita as sugestões
da SLM em português brasileiro; `en` faz o mesmo em inglês. O CLI não pode
sobrescrever essa política localmente.

A SLM tenta preencher objetivo, arquitetura/pré-requisitos, possíveis impactos e
comandos de rollback. Todo trecho sugerido aparece marcado como **REVISÃO
OBRIGATÓRIA**. O Lucien não executa comandos. Trate o conteúdo como um ponto de
partida não confiável e valide alvo, impacto, permissões e retorno antes de publicar.
Cada comando selecionado leva junto sua saída real sanitizada. São mantidas as cinco
primeiras linhas; saídas maiores mostram também `...` e a última linha. Desmarcar um
comando exclui comando, saída e impacto do rascunho.

Cada passo deve manter o cabeçalho imediatamente seguido pelo bloco `bash`:

````markdown
> **REVISÃO OBRIGATÓRIA — COMANDO CAPTURADO:** não execute sem validar alvo, impacto, permissões e plano de retorno.

> **REVISÃO OBRIGATÓRIA — SUGESTÃO DA SLM:** possível impacto: consulta o estado da replicação sem alteração esperada. Valide antes de executar.

### Passo 1: Verificar a replicação
```bash
redis-cli info replication
```
```text
role:slave
master_link_status:up
```
````

Não adicione YAML Frontmatter: autor, papel, função, data e tags são injetados
exclusivamente pelo Hub. Ao fechar o editor, o rascunho é guardado localmente com
permissões restritas.

O título remove somente o sufixo técnico criado pelo Lucien. Na publicação, o
arquivo combina esse nome legível com o UUID completo, por exemplo
`teste-uso_1--b8b6e6a1-5bd9-47cc-8a50-df1bea1a4055.md`. O provider nunca
sobrescreve conteúdo divergente. GitHub, Gitea e a página local usam a mesma
hierarquia por domínio confiável e ano, por exemplo:
`docs/runbooks/servidores/2026/teste-uso_1--b8b6e6a1-5bd9-47cc-8a50-df1bea1a4055.md`.

## 10. Publicar

```bash
lucien job sent <id_ou_nome_ou_indice>
```

O comando gera uma chave de idempotência a partir do usuário, Job e conteúdo.
Repetir a publicação após um timeout não cria outro documento quando o conteúdo é
idêntico. Se o destino de publicação falhar, edite o rascunho livremente e envie
de novo: enquanto o Job estiver `PENDING`, a nova tentativa substitui a reserva
anterior usando a nova chave derivada do conteúdo. Reutilizar uma chave com outro
conteúdo retorna `409`; após `PUBLISHED`, o conteúdo é imutável. O Hub ainda
executa estas
barreiras:

1. autenticação e verificação de propriedade;
2. secret scanning obrigatório; detecção ou indisponibilidade bloqueiam a ação;
3. sanitização final do Markdown pela DLP;
4. validação da gramática dos passos;
5. RBAC de criticidade;
6. geração server-side do Frontmatter;
7. publicação pelo provider configurado.

Se o Hub substituir dados sensíveis, o CLI informa apenas a quantidade de
substituições, nunca os valores encontrados.

## 11. Expurgar ou cancelar um Job

```bash
lucien job del <id_ou_nome_ou_indice>
```

Use `--yes` apenas em automação controlada:

```bash
lucien job del <id_ou_nome_ou_indice> --yes
```

Para abandonar um processamento em andamento:

```bash
lucien job del <id_ou_nome_ou_indice> --force
```

Somente Jobs próprios podem ser afetados. Sem `--force`, apenas `PENDING` e
`FAILED` são apagados. Publicações são imutáveis mesmo com `--force`.

## Ambiente distribuído

No servidor:

```bash
cp .env.server.example .env
docker compose --profile tools run --rm certgen
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  --profile server build
docker compose --profile server up -d
```

No cliente:

```bash
export API_HOST="https://runbook.exemplo.interno:8443"
export TLS_CA_FILE="/etc/lucien/ca.crt"
lucien login
```

Antes de subir, ajuste `API_HOST` para o FQDN do Hub, inclua o mesmo FQDN em
`CERT_DNS` e instale a CA no volume do cliente. Libere somente TCP 8443 entre as
origens necessárias.

## Escolher o destino

O caminho recomendado é responder ao `deploy/install-hub.sh`, que gera um único
preset coerente. As quatro escolhas são:

| Escolha | Quando usar | Executor adicional |
| --- | --- | --- |
| local + portal | runbooks permanecem no host do Hub | nenhum; portal HTTPS/9091 |
| GitHub | documentação no GitHub Pages | runner hospedado pelo GitHub |
| Gitea compacto | instalação prática no host do Hub | builder fixo, sem Docker socket |
| Gitea runner | organização com VM de CI dedicada | `act_runner` na VM dedicada |

Para disco local com portal e revisão controlada:

```dotenv
COMPOSE_PROFILES=consolidated,local-viewer
STORAGE_PROVIDER=local
LOCAL_STORAGE_ROOT=/data/playbooks
VIEWER_BIND_ADDRESS=0.0.0.0
```

O segredo de sessão é gerado em `secrets/viewer_session_secret`, nunca no `.env`.

Abra `https://<FQDN-do-Hub>:9091`. Informe seu username e token pessoal; o nome
sozinho não autentica. A página usa o `logo-lucien.png` da distribuição, agrupa runbooks
por função/tags e permite alternar tema claro/escuro. Libere TCP/9091 somente para
as redes leitoras.

Todos os usuários Lucien ativos podem visualizar o catálogo. A opção **Editar**
aparece apenas para `admin` e, no próprio domínio, `senior`. Edite somente o corpo
Markdown e mantenha a gramática `### Step`/bloco `bash`; o portal não envia
frontmatter. Ao salvar, o Hub autentica novamente, aplica DLP, secret scanning e
RBAC e cria uma nova revisão imutável. A versão anterior continua no disco e na
trilha de auditoria.

Se a página informar que a versão mudou, recarregue antes de reaplicar sua
alteração. Em indisponibilidade temporária do destino, use **Tentar novamente** na
mesma tela para conservar o conteúdo e a chave idempotente; não abra outra aba.

Para GitHub:

```dotenv
STORAGE_PROVIDER=github
GIT_API_BASE=https://api.github.com
GIT_OWNER=sua-organizacao
GIT_REPO=runbooks
GIT_BRANCH=main
GIT_DOCS_PREFIX=docs/runbooks
```

Selecione **GitHub Actions** como fonte do Pages. Não instale runner próprio para
este modo. No GitHub.com, repositório privado é aceito nos planos Pro, Team e
Enterprise Cloud, porém
o site só pode ter acesso privado quando pertence a uma organização no GitHub
Enterprise Cloud. Confirme **Settings → Pages → Visibility → Private** antes de
enviar documentação interna; caso essa opção não exista, o site deve ser tratado
como público. Workflows de repositório privado consomem a franquia de minutos do
plano e podem gerar cobrança após o limite. O workflow entregue não suporta
GitHub Enterprise Server.

O deploy do Pages usa apenas o `GITHUB_TOKEN` temporário. Para o Hub publicar no
repositório privado, use outro token *fine-grained*, restrito ao repositório e com
**Contents: Read and write**, em `GIT_TOKEN`; não conceda Actions ou Pages a esse
token. O workflow valida Pull Requests e somente publica a partir de `main`.

Para Gitea compacto, além das variáveis do provider, configure:

```dotenv
COMPOSE_PROFILES=consolidated,gitea-compact
STORAGE_PROVIDER=gitea
GIT_API_BASE=https://gitea.exemplo.interno/api/v1
GIT_CA_SOURCE=./certs/gitea-ca.crt
WIKI_REPOSITORY_URL=https://gitea.exemplo.interno/infraestrutura/runbooks.git
WIKI_REPOSITORY_BRANCH=main
WIKI_REPOSITORY_USER=lucien-wiki-reader
WIKI_BIND_ADDRESS=127.0.0.1
```

O token somente leitura fica em `secrets/wiki_repository_token`. O token de
escrita do Hub fica separadamente em `secrets/git_token`.

O `GIT_TOKEN` do Hub continua sendo a credencial de escrita; não o reutilize no
builder. O modo compacto não usa Gitea Actions. Para acesso remoto à wiki em
9092, configure um proxy HTTPS na frente do bind local.

Para Gitea runner, use o mesmo provider, escolha o quarto preset e só então
execute `--configure-gitea-runner` na VM dedicada. Injete todos os tokens por um
gerenciador de secrets, nunca em arquivo versionado.

## Solução de problemas

| Sintoma | Verificação |
| --- | --- |
| `API_HOST não configurada` | confirme que o `.env` correto está carregado |
| erro de CA ou hostname | confira `TLS_CA_FILE`, `CERT_DNS` e o hostname de `API_HOST` |
| `401` | token inválido, revogado ou bootstrap já fechado |
| `403` na publicação | propriedade do Job ou política RBAC não atendida |
| `403` ao revisar no portal | somente admin ou senior do domínio pode criar revisão; a interface revalida isso no Hub |
| `404` na API de revisão | ID inexistente ou runbook fora do domínio do senior; o Hub não diferencia os casos |
| `412` ao revisar no portal | outra revisão mudou a base; recarregue antes de editar |
| `409` ao revisar no portal | a base já possui sucessor ou a chave não corresponde à tentativa; repita o formulário original ou recarregue |
| `422` com política de segredos | remova a credencial real; use um placeholder como `SUA_SENHA_AQUI` |
| `502` com secret scanner indisponível | restaure o serviço; o Hub bloqueia por segurança |
| nenhum comando detectado | forneça `-d`, reduza ruído do terminal e confirme a saúde da SLM |
| aviso de log truncado no `stop`/`upload` | a sessão excedeu `MAX_LOG_BYTES`; comandos do fim podem faltar — grave sessões mais curtas ou eleve o limite |
| editor não abre | configure `EDITOR=vi`, `vim` ou outro executável disponível |
| publicação retorna conflito | o Job já está `PUBLISHED`, a mesma chave foi reutilizada com outro conteúdo ou o destino já contém um artefato divergente |

Para detalhes de segurança e limitações, continue em
[Documentação técnica](documentacao-tecnica.md).
