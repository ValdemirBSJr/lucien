# Operação e segurança

## Redação de um runbook

Um procedimento deve explicar objetivo, pré-requisitos, impacto, comandos,
validação e rollback. Comandos destrutivos precisam trazer uma confirmação
explícita e um critério de interrupção.

Cada passo deve seguir exatamente esta gramática, sem linha vazia entre o
cabeçalho e o bloco:

````markdown
### Step 1: List containers
```bash
docker ps
```
> Confirme que os contêineres esperados estão ativos.
````

O CLI usa `### Step X: Action`; documentos anteriores com
`### Passo X: Ação` continuam válidos. Passos devem começar em 1 e ser
sequenciais. O Hub rejeita blocos `bash` soltos, passos sem comando e frontmatter
enviado pelo cliente.

Blocos de código genéricos — `yaml`, `json` ou exemplos aninhados em
` ```` ` — são tratados como conteúdo literal: o que estiver dentro deles não
participa da gramática dos passos. Todo fence aberto precisa ser fechado; um
bloco sem fechamento é rejeitado.

## Sanitização

O Hub aplica a mesma política em três fronteiras:

- antes de enviar o log ao SLM;
- sobre os comandos devolvidos pelo SLM, antes de criar o Job;
- depois da revisão humana e antes da persistência do Markdown.

São neutralizados cabeçalhos `Authorization`, credenciais embutidas em URLs,
chaves privadas PEM, tokens conhecidos, o comando `AUTH` do Redis em posição de
comando (início de linha, após prompt Redis terminado em `>` ou em linha iniciada
por `redis-cli`) e variáveis ou flags com nomes de senha, token, segredo ou chave.
Headings Markdown, blockquotes e prosa que apenas mencionam a palavra "auth" não
são alterados. O CLI informa a quantidade de
substituições realizadas na publicação, sem registrar os valores removidos.

Exemplo:

```dotenv
REDIS_USER=SEU_USER_REDIS_AQUI
REDIS_PASSWORD=SUA_SENHA_REDIS_AQUI
EVOLUTION_API_KEY=SUA_KEY_EVOLUTION_AQUI
```

!!! danger "Limite da proteção"
    Expressões regulares reduzem exposição acidental, mas não substituem DLP nem
    secret scanning. Credenciais posicionais ou formatos proprietários podem
    escapar; mantenha revisão humana e um scanner de segredos como barreira
    independente no repositório.

## Aprovação

Proteja a branch `main` no provedor Git, exija Pull Request e pelo menos uma
aprovação. O provedor de armazenamento atual usa a API de Contents e escreve
diretamente em `GIT_BRANCH`; portanto, essa proteção exige uma evolução para criar
branch e Pull Request, ou um bypass de serviço estreitamente limitado. Liberar
push irrestrito contradiz o requisito de aprovação.

## Fila de upload e worker

O Hub aceita o upload antes da inferência. Acompanhe o consumidor e os estados
sem registrar payloads:

```bash
docker compose --env-file .env -f docker-compose.local.yml ps hub upload-worker slm
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 upload-worker
lucien job status <JOB_ID>
```

`UPSTREAM_ERROR` indica tentativas esgotadas contra SLM ou scanner. Corrija a
dependência e use `lucien job retry <JOB_ID>`. Não faça retry em loop externo: o
worker já aplica backoff exponencial e lease durável.

É possível elevar `LUCIEN_SLM_CPU_LIMIT` até o total disponibilizado ao Docker e
recriar apenas `slm`. Réplicas de `upload-worker` são seguras por `SKIP LOCKED`,
mas não aumentam throughput quando todas disputam uma única SLM limitada a uma
inferência por vez.

Se o limite de CPU for menor que o total de CPUs do host, iguale
`SLM_NUM_THREAD` a ele. O Ollama dimensiona as threads pelo host e ignora a cota
do cgroup; threads em excesso gastam o período em espera ativa e o contêiner é
throttled, encarecendo cada token.

## Quando a SLM não sustenta o enriquecimento

O upload faz duas chamadas à SLM: a extração de comandos, que é insubstituível, e
o enriquecimento, que é auxiliar. Em host lento, a segunda é a primeira a não
caber em `SLM_TIMEOUT_SECONDS`. Três saídas, da mais pontual para a mais ampla:

- `lucien upload -s` ou `lucien job retry <JOB_ID> -s` dispensam o enriquecimento
  daquele Job. O rascunho sai com a estrutura básica e o operador redige
  objetivo, validação e rollback na revisão.
- `SLM_ENRICHMENT_ENABLED=false` aplica o mesmo efeito a todos os uploads.
- `RUNBOOK_ENRICHER=deterministic` mantém o enriquecimento, mas sem modelo: tags,
  pré-requisitos, impactos e rollback saem de tabelas revisáveis. É instantâneo e
  não pode alucinar, mas cobre apenas comandos conhecidos pelas tabelas.

Mesmo habilitado, uma falha de upstream no enriquecimento não derruba o Job: o
worker registra `job.enrichment_skipped` e preserva a extração concluída. A
extração continua dependendo da SLM em qualquer um dos casos acima.

Meça a taxa real antes de culpar o modelo. O tamanho do modelo só importa se a
geração escalar com ele: se um modelo seis vezes menor render a mesma quantidade
de tokens por segundo, o gargalo não é o modelo, e trocar por um menor não vai
ajudar.

Um Job `PROCESSING` cujo trabalho deva ser abandonado pode ser cancelado pelo
proprietário com `lucien job del <JOB_ID> --force`. O worker trata a remoção
concorrente como cancelamento esperado; `PUBLISHED` permanece imutável.

!!! warning "Rotação de AUTH_PEPPER"
    A mesma raiz deriva HMACs de tokens e a chave AES-GCM da fila. Antes de
    rotacioná-la, aguarde Jobs `PROCESSING` terminarem e resolva ou expurgue Jobs
    `FAILED`. Payloads cifrados com a chave anterior não podem ser recuperados
    depois da rotação; isso é intencional e evita fallback inseguro.

## Dimensionar o secret-scanner

Cada varredura é um subprocesso do gitleaks. Duas variáveis limitam quantos
existem ao mesmo tempo e quanto tempo uma requisição espera por vaga:

| Variável | Efeito | Padrão |
|---|---|---|
| `SCANNER_MAX_CONCURRENCY` | processos simultâneos | `4` |
| `SCANNER_QUEUE_TIMEOUT_SECONDS` | espera máxima por vaga | `10` |

O limite importa mais do que parece. A política do Hub é **fail-closed**:
scanner indisponível bloqueia publicação. Um lote de uploads que exaure o
contêiner não prejudica só a si — para a publicação da instalação inteira.

Saturado, o serviço responde `503` em vez de enfileirar sem limite. O Hub trata
isso como falha de upstream e reagenda o Job com backoff, o que é melhor que
pendurar conexões até estourarem em outro lugar.

Para dimensionar, comece pelas CPUs concedidas ao contêiner. O gitleaks é
limitado por CPU, então mais processos que núcleos só troca throughput por
troca de contexto:

```
SCANNER_MAX_CONCURRENCY  ≈  CPUs do contêiner
```

Observe sob carga real antes de aumentar:

```bash
docker compose --env-file .env -f docker-compose.local.yml exec secret-scanner sh -c 'ps -o pid,etime,comm | grep -c gitleaks'
```

```bash
docker stats --no-stream lucien-runbook-secret-scanner-1
```

Se aparecerem `503` com "saturado" no log do worker e a contagem de processos
ficar colada no teto, aumente. Se a memória subir sem o throughput acompanhar,
reduza.

O `pids_limit: 64` no Compose é a última barreira, não a primeira: o semáforo
da aplicação é quem deve segurar: o limite do contêiner existe para o caso de
um defeito no semáforo, e derruba o serviço em vez do host.

### Prontidão separada de vivacidade

`/health` responde que o processo está de pé. `/ready` verifica que o gitleaks
pode ser executado, rodando `version` — sem entrada e sem achado, então nada
sensível passa pelo processo ou pelo log.

A separação importa porque um serviço vivo com o binário quebrado aceitaria
requisições e responderia `503` em todas. Sendo fail-closed, isso pararia a
publicação sem que o orquestrador tirasse a réplica de rotação. O healthcheck
do Compose consulta `/ready`.

## Dimensionar a janela de contexto da SLM

Duas variáveis governam quanto a SLM lê por Job, e ambas dependem do hardware:

| Variável | Efeito | Padrão |
|---|---|---|
| `SLM_NUM_CTX` | janela de contexto pedida ao runtime | `8192` |
| `SLM_PROMPT_MAX_CHARS` | teto do log reduzido enviado ao modelo | `8000` |

### Por que `SLM_NUM_CTX` precisa ser explícito

Sem essa variável o runtime usa o próprio padrão — **2048 tokens** — e descarta
em silêncio o que não couber. O corte vem do início do prompt, que é justamente
onde fica a instrução de responder apenas JSON. O modelo então responde qualquer
coisa e o Job falha com `UPSTREAM_ERROR`, sem nada no log indicando truncagem.

O sintoma é reconhecível: `prompt_eval_count` travado em ~2050 mesmo com um log
grande. Para medir:

```bash
docker compose --env-file .env -f docker-compose.local.yml exec slm \
  sh -c 'ollama ps'
```

Note que `ollama show <modelo> | grep context` informa o que o **modelo
suporta**, não o que o runtime **aloca**. Os dois números são diferentes e só o
segundo importa aqui.

### Quanto de memória custa

A janela reserva um cache proporcional ao seu tamanho. A regra prática:

```
memória do cache ≈ 2 × camadas × dim_kv × num_ctx × 2 bytes
```

Em vez de estimar, meça: suba com o valor pretendido e observe o contêiner.

```bash
docker compose --env-file .env -f docker-compose.local.yml exec slm ollama ps
```

```bash
docker stats --no-stream lucien-runbook-slm-1
```

A coluna `SIZE` do `ollama ps` já inclui o cache da janela configurada. Se ela
se aproximar do limite de memória do contêiner, reduza `SLM_NUM_CTX`.

### Como escolher os dois valores

Comece pelo prompt, não pela janela. O Hub já colapsa blocos de saída antes de
enviar — uma sessão de captura de pacotes com centenas de linhas costuma chegar
a poucas centenas de caracteres. `SLM_PROMPT_MAX_CHARS` é o teto final, para o
caso de uma sessão com muitos comandos.

Converta o teto em tokens dividindo por três, que é uma aproximação conservadora
para texto de terminal, e acrescente folga para o prompt do sistema e para a
resposta:

```
SLM_NUM_CTX  ≥  (SLM_PROMPT_MAX_CHARS ÷ 3)  +  512
```

Com os padrões: 8000 ÷ 3 ≈ 2700, mais 512, dá ~3200. O padrão de 8192 deixa
folga confortável e ainda é modesto em memória.

Três situações pedem ajuste:

**Host com pouca memória.** Reduza os dois em conjunto, mantendo a relação
acima: `SLM_PROMPT_MAX_CHARS=3000` com `SLM_NUM_CTX=2048`, por exemplo.

**Host lento.** O tempo de cada tentativa cresce com o número de tokens do
prompt. Reduzir `SLM_PROMPT_MAX_CHARS` barateia cada tentativa sem perder
comando — a redução preserva todas as linhas de comando e só encolhe a saída.

**Sessões muito longas.** Se o operador costuma capturar dezenas de comandos,
aumentar `SLM_PROMPT_MAX_CHARS` dá mais contexto ao modelo. Aumente `SLM_NUM_CTX`
junto, pela fórmula, senão a truncagem silenciosa volta.

### O que não muda com esses valores

A redução vale apenas para o prompt do modelo. O filtro de comandos observados,
o passe de recall por prompt e a extração de saída continuam recebendo o log
completo. Um valor mal escolhido pode piorar a ordenação sugerida pela SLM, mas
não faz nenhum comando desaparecer do runbook nem altera os blocos de saída
publicados.


## Cópia de segurança e recuperação

O banco guarda identidade, RBAC, a fila cifrada e a linhagem das publicações.
Os artefatos publicados vivem fora dele — no Git ou no volume — e sobrevivem à
perda do banco, mas ficam órfãos: continuam legíveis e não aparecem no
catálogo, porque o catálogo é uma consulta ao banco.

### Gerar

```bash
BACKUP_DIR=/mnt/backup scripts/backup-db.sh
```

O script gera um dump em formato custom, **verifica que o arquivo pode ser
lido** e só então aplica a retenção. A ordem importa: apagar as antigas antes
de validar a nova deixaria uma janela sem cópia alguma.

| Variável | Efeito | Padrão |
|---|---|---|
| `BACKUP_DIR` | destino das cópias | `./backups` |
| `BACKUP_RETENTION` | quantas manter; `0` desliga a remoção | `14` |
| `BACKUP_ENCRYPT_KEY_FILE` | arquivo com a senha de cifra | vazio |

Sem `BACKUP_ENCRYPT_KEY_FILE` o dump fica em texto claro, protegido apenas
pela permissão do sistema de arquivos — o diretório nasce `0700` e o arquivo
`0600`, e o script avisa. O dump carrega hashes de credencial e a fila cifrada;
trate-o como material sensível independentemente da opção escolhida.

### Provar que a cópia serve

Uma cópia nunca restaurada é uma hipótese, não um plano de recuperação.

```bash
scripts/test-restore.sh
```

Sem argumento, usa a mais recente. O teste sobe um PostgreSQL descartável **sem
rede**, restaura ali e confere que as tabelas de identidade, Jobs e fila
chegaram, que existe ao menos um admin ativo e que as constraints vieram junto.
Nada toca a instalação.

Um Hub restaurado sem admin ativo não pode ser administrado: a recuperação
estaria incompleta mesmo com todas as tabelas presentes. Por isso essa
verificação existe.

### Restaurar em produção

```bash
scripts/restore-db.sh /mnt/backup/lucien-20260820T210000Z.dump
```

**Operação destrutiva.** Exige digitar `RESTAURAR`, e para o Hub e o worker
antes — restaurar com eles escrevendo produz um estado que não corresponde nem
à cópia nem ao que havia. Se a restauração falhar, os dois permanecem parados
de propósito: subir sobre um banco meio restaurado é pior que ficar fora do ar.

Prove a cópia com `test-restore.sh` **antes** de usar este comando.

### Objetivos e responsabilidades

| | |
|---|---|
| **RPO** | igual ao intervalo entre execuções do `backup-db.sh`. Sem agendamento, é o tempo desde a última execução manual. |
| **RTO** | minutos: parar os serviços, restaurar e subir. Dominado pelo tamanho do dump. |
| **Responsável** | quem opera a instalação. Não há automação de agendamento nem destino remoto. |
| **Escopo** | somente o banco. Artefatos publicados dependem do backup do Git ou do volume `playbooks-data`. |

Para reduzir o RPO, agende o script no host — por exemplo, uma entrada de cron
diária. O projeto não instala esse agendamento porque a frequência é decisão de
quem opera, e um cron silencioso que falha é pior que a ausência dele.

### Evidência do último teste

Registre aqui a cada execução de `test-restore.sh`, para que a data seja
verificável e não uma lembrança:

| Data | Arquivo | Resultado |
|---|---|---|
| _(preencher)_ | | |

## Portões de qualidade antes de publicar

A implantação é manual: os arquivos são copiados para o servidor à mão. Um CI
que dispara no push não protege esse momento, então os mesmos portões existem
como script, para rodar antes da cópia.

```bash
bash scripts/verify.sh
```

Ele executa onze portões — imagens de teste do backend, do portal, do
wiki-builder e do secret-scanner; migrações contra um PostgreSQL descartável;
`gofmt`, `go vet` e `go test` do CLI; sintaxe e ShellCheck dos scripts;
`docker compose config` mais a conferência da segmentação de rede;
`mkdocs --strict`; Ruff e mypy sobre o Python. Cada um roda até o fim; o
veredito sai depois de todos, para que uma única execução mostre tudo que
precisa de conserto.

Para trabalhar num portão específico:

```bash
bash scripts/verify.sh backend
```

Um portão que dependa de ferramenta ausente na máquina — `go`, `mkdocs` — é
marcado como ignorado, não como aprovado. Ferramenta ausente não é evidência
de que o código está bom.

Os mesmos portões estão em `.gitea/workflows/ci.yml` e no espelho para GitHub,
para quando houver runner. O script é a fonte de verdade dos comandos, para os
dois não divergirem.

## Conferir a implantação depois de subir

`verify.sh` valida o **código**. O que roda no servidor é outra coisa, e as
duas divergem em silêncio: numa única noite o `docker-compose.local.yml` estava
anterior à segmentação de rede, uma imagem tinha perdido um arquivo que o
serviço lê a cada ciclo, e o fonte de um serviço no servidor era semanas mais
antigo que o repositório -- mascarado por um healthcheck que apontava para uma
rota existente nas duas versões. Nenhum dos onze portões viu nada, porque
nenhum olha o que está no ar.

Rode no host do Hub, a partir da raiz da instalação, depois de qualquer
implantação:

```bash
bash scripts/verify-deploy.sh
```

Ele compara cinco coisas contra o repositório:

| Verificação | O que pega |
| --- | --- |
| `docker-compose.local.yml` idêntico à base | configuração que "subiu" mas não foi regenerada |
| marcador no fonte de cada serviço | arquivo que nunca foi copiado para o servidor |
| rota viva na imagem em execução | imagem antiga rodando com fonte novo no disco |
| `schema_migrations` completo | migração faltando |
| conexão real para fora | segmentação que existe no arquivo e não no ar |

As duas últimas linhas são medidas, não lidas. Perguntar ao arquivo do Compose
se o banco tem saída para a internet foi exatamente o que deixou passar --
estava certo no repositório e errado na máquina. O script abre a conexão.

Serviço fora do ar vira aviso, não falha: um perfil desligado não é
divergência. Falha é o que o repositório e a máquina discordam.

Quando um serviço novo entrar, acrescente o marcador dele em `conferir_fonte`.
Um marcador é qualquer símbolo que só existe na versão atual do arquivo.

## Diagnóstico do builder da wiki

Um `wiki-builder` `Up` e `unhealthy` significa que nenhum ciclo concluiu nos
últimos minutos. O healthcheck exige uma publicação recente **e** o link
`current`; se ele nunca existiu, o builder jamais promoveu uma release e o
`wiki-static` está servindo a página padrão do Nginx, não conteúdo antigo.

```bash
docker compose --env-file .env -f docker-compose.local.yml exec wiki-builder ls -la /publish
```

Procure por `current`. Para a causa, agrupe o log em vez de lê-lo linha a linha:

```bash
docker compose --env-file .env -f docker-compose.local.yml logs --tail=2000 wiki-builder | grep -oP 'preservada: \K.*' | sort | uniq -c | sort -rn
```

Note que `restart: unless-stopped` **não** reage a `unhealthy` — só o Swarm faz
isso. Um builder falhando fica de pé indefinidamente, então vale incluí-lo na
verificação periódica em vez de confiar no reinício automático.

## Portal dos runbooks locais

Com o preset `local-viewer`, acesse `https://<host-do-hub>:9091` e autentique-se
com o mesmo username e token pessoal usados pelo CLI. Username sozinho não é uma
credencial. O portal revalida o token no Hub em cada página, renderiza apenas
arquivos válidos do volume e nunca escreve neles diretamente.

Usuários `junior` e `pleno` possuem somente leitura. Um `senior` pode abrir
**Editar** apenas em runbooks do próprio domínio; um `admin` pode revisar qualquer
runbook local. A decisão efetiva ocorre novamente no Hub. Cada salvamento cria
uma revisão imutável com novo ID e mantém a versão anterior para auditoria.

O tema segue a preferência claro/escuro do sistema na primeira visita. O botão do
cabeçalho grava somente a escolha visual no `localStorage`; identidade, token e
conteúdo não são gravados ali. Para diagnosticar sem revelar credenciais:

```sh
docker compose --env-file .env -f docker-compose.local.yml ps runbook-viewer
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 runbook-viewer
```

Não publique 9091 na Internet. Restrinja a porta às redes leitoras e faça backup
do volume `playbooks-data` com uma ferramenta que preserve permissões POSIX. O
portal é stateless; suas réplicas podem compartilhar o mesmo volume somente
leitura e o mesmo segredo de sessão.

O editor protege concorrência com `If-Match`. Se receber `412`, outra pessoa já
publicou uma revisão: recarregue e reaplique a mudança sobre a versão atual. Em
falha `502`, não reabra o editor; repita na mesma página para preservar a chave
idempotente e o corpo da tentativa. A interface retorna `403` quando o papel ou o
domínio visível não permite editar. Se alguém chamar diretamente a API do Hub,
um `senior` de outro domínio recebe `404` para não confirmar a existência do
runbook. Nunca contorne essas respostas alterando o frontmatter: metadados do
arquivo não concedem privilégio.

## Operação do Gitea compacto

O builder faz polling da branch e só promove uma release completa. Uma falha de
clone ou build não remove `current`; corrija a causa e deixe o retry processar o
mesmo commit. Acompanhe os dois serviços sem acessar PostgreSQL ou SLM:

```sh
docker compose --env-file .env -f docker-compose.local.yml ps wiki-builder wiki-static
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 wiki-builder
```

`wiki-volume-init` deve terminar com `Exited (0)`: ele ajusta somente a
propriedade e os modos dos volumes para o UID `10004` antes de builder e Nginx
iniciarem. O job não possui rede e conserva apenas `CHOWN` e `FOWNER` durante a
execução. O
builder permanece sem root e sem capabilities. Se uma instalação anterior
registrar `Permission denied: '/publish/.builder.lock'`, atualize o Compose e
recrie os serviços do modo compacto:

```sh
docker compose --env-file .env -f docker-compose.local.yml \
  -f docker-compose.build.yml build wiki-builder
docker compose --env-file .env -f docker-compose.local.yml \
  up -d --force-recreate wiki-volume-init wiki-builder wiki-static
```

O Nginx compacto escuta em `127.0.0.1:9092` por padrão. Use um proxy HTTPS
administrado para acesso remoto. Não monte o socket Docker e não substitua a
configuração MkDocs fixa por arquivos do repositório.
