# IAM, RBAC e metadados confiáveis

## Autoridade

O Hub é a única autoridade de identidade. Em cada requisição, o token Bearer é
convertido em HMAC-SHA-256 com `AUTH_PEPPER`, consultado no banco e transformado
em um `SecurityContext` contendo ID, username, nível, função e estado.

O CLI não envia nem persiste seu papel ou função. Usuários revogados recebem
`401` na requisição seguinte, pois a identidade é consultada a cada chamada.

## Bootstrap e administração

`POST /bootstrap/admin` é uma exceção controlada para criar o primeiro admin.
Exige `USER_CREATION_ENABLED=true`, a chave de bootstrap e o latch persistente
ainda aberto. A criação e o fechamento do latch ocorrem na mesma transação, de
modo que múltiplos workers ou réplicas não criem dois primeiros admins. O latch
não reabre se um admin for revogado. Desabilite a janela após o uso.

Somente um token admin pode usar:

| Método | Endpoint | Finalidade |
| --- | --- | --- |
| `POST` | `/admin/users` | criar usuário e emitir credencial provisória |
| `POST` | `/admin/users/{id_ou_username}/provisional-token` | substituir a credencial por outra provisória |
| `PATCH` | `/admin/users/{id_ou_username}` | alterar papel e função |
| `DELETE` | `/admin/users/{id_ou_username}` | revogar o usuário |
| `POST` | `/admin/users/{id_ou_username}/reinstate` | readmitir um usuário revogado |
| `POST` | `/auth/exchange` | trocar credencial provisória por permanente |
| `GET` | `/me` | validar token e consultar a identidade corrente |

Admins não podem alterar ou revogar a própria identidade por esses endpoints.

## Emissão, rotação e recuperação de tokens

O primeiro administrador é criado por `lucien create user`. O Hub devolve uma
credencial permanente `luc_...`; o CLI a exibe uma vez e tenta salvá-la no
keyring da conta do sistema operacional. Depois, valide com `lucien auth status`.
Não execute esse comando dentro de uma sessão gravada.

Um administrador autenticado gerencia as identidades seguintes pelo CLI:

```bash
lucien admin user create operador --role junior --domain servidores
lucien admin user update operador --role pleno --domain servidores
lucien admin user issue-provisional-token operador
lucien admin user revoke operador --yes
lucien admin user reinstate operador --yes
```

A criação mostra uma credencial provisória `luc_tmp_...`, válida por quatro horas
e para uma única troca. Entregue-a por cofre ou canal corporativo aprovado. O
usuário executa `lucien login` e cola o valor no prompt sem eco. O Hub consome a
credencial provisória de forma atômica, emite uma permanente e o CLI a exibe uma
vez e a salva localmente. O CLI usa `Idempotency-Key`: se a resposta se perder,
repete a mesma troca e recebe a mesma permanente. Outra chave recebe `401`.

`issue-provisional-token` repete o fluxo quando a credencial permanente é
perdida. A emissão invalida imediatamente a credencial permanente anterior e
qualquer provisória ainda pendente. Não repita o comando automaticamente: cada
execução intencional cria uma nova credencial e torna a anterior inutilizável.
As respostas usam `Cache-Control: no-store`; o Hub guarda somente HMAC e prazo.

O Hub recusa deixar o conjunto de administradores vazio: o último admin ativo
não pode ser revogado nem rebaixado, e a recusa vem com `409`. A checagem roda
dentro da mesma transação que grava a mudança, então dois administradores que
se revoguem ao mesmo tempo não passam ambos -- um grava, o outro recebe o
conflito. Rebaixar conta tanto quanto revogar: as duas saídas tiram um admin.

Perder a credencial do último administrador não reabre o bootstrap. Um operador
com acesso administrativo ao host recupera esse admin diretamente no contêiner:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec hub python -m app.recover_admin Admin
```

O comando exige um admin ativo, aceita UUID ou username e emite uma credencial
provisória de quatro horas. Papel, domínio, Jobs e publicações não são alterados.
Use-a imediatamente com `lucien login`. A operação registra
`user.recover_provisional_token` sem registrar a credencial.

`secrets/auth_pepper` deve permanecer estável e protegido por backup. Sua troca
invalida todos os tokens existentes; nesse caso, recupere o primeiro admin
offline e use-o para rotacionar as demais credenciais.

## Terminal pessoal e jump server

Em uma estação pessoal, `lucien login` guarda a credencial no keyring da conta
local. Em um jump server, cada pessoa deve possuir uma conta Unix própria e
executar o mesmo login nessa conta. Se o keyring não estiver disponível, o
fallback em arquivo só pode ser habilitado em host controlado; ele usa diretório
`0700` e arquivo `0600`.

Não use uma conta Unix compartilhada: nesse modelo os usuários também
compartilhariam o perfil, o cofre e os rascunhos, portanto não existe isolamento
confiável. O username local é apenas informativo; cada chamada remota revalida a
credencial no Hub, que determina a identidade e os metadados do runbook.

No modo automatizado do jump server, uma credencial M2M separada possui somente
o escopo `jump_enrollment`. Ela não acessa Jobs nem rotas administrativas. O Hub
correlaciona o ID POSIX (`U000001`, por exemplo) ao username Lucien, cria novos
usuários sempre como `pleno` e aceita apenas `acessos`, `servidores`, `redes` ou
`suporte`. Papel e domínio de uma identidade existente nunca são alterados pelo
helper: `junior`, `pleno` e `senior` preservam seus escopos. Contas `admin` usam
exclusivamente o login administrativo e nunca são ativadas pelo M2M. O token
provisório passa ao CLI por `stdin`; não aparece em argumentos,
variáveis de ambiente, arquivos de shell ou logs.

Essa política é opt-in e exclusiva do host jump: somente
`LUCIEN_JUMP_MODE=true` ativa a correlação com a conta POSIX. Estações pessoais,
WSL e outros hosts continuam usando `lucien login` e o token individual sem M2M
ou dependência de LDAP, preservando o modo distribuído original.

No modo jump, o CLI compara a identidade retornada por `/me` com o usuário
esperado e bloqueia `start`, `upload`, `reviews`, `job`, `admin` e `create` em
caso de falha. `stop` permanece localmente disponível para não perder uma
captura já iniciada. Esse bloqueio é uma proteção operacional; a autorização
real continua no Hub a cada requisição.

## Publicação

O payload aceita exclusivamente `{"markdown": "CORPO_REVISADO"}`. Frontmatter
no corpo ou campos extras de identidade são rejeitados. O Hub sanitiza, valida a
gramática, aplica RBAC, congela a identidade para retries idempotentes e gera:

```yaml
---
id: "<job_id>"
autor: "<username_extraido_do_token>"
nivel_autor: "<role_level>"
funcao: "<domain_function>"
data_criacao: "<iso_8601>"
tags_inferidas: ["<tags_geradas_pela_SLM>"]
versao: "1"
ultimo_revisor: ""
data_revisao: ""
---
```

Uma revisão acrescenta `runbook_raiz`, `revisao` e `substitui`, e preenche os dois
últimos campos com quem a publicou e quando. Os quatro campos de procedência
(`autor`, `nivel_autor`, `funcao`, `data_criacao`) são os da **raiz**, copiados da
primeira versão: eles descrevem o runbook, não a versão. `funcao` em particular tem
que ser a da raiz, porque é ela que decide o diretório de destino — publicar a área
do revisor faria o documento contradizer a pasta onde está.

A SLM só infere tags. Ela nunca determina autorização. Criticidade alta é
classificada por regras determinísticas sobre comandos destrutivos; um usuário
`junior` não pode publicá-la.

No portal local, `junior` e `pleno` permanecem somente leitura. `admin` pode
revisar qualquer runbook e `senior` somente documentos cujo `funcao` confiável
congelado no banco coincide com seu `domain_function` atual. O Hub não usa o
frontmatter apresentado pelo portal como fonte de autorização.

## RBAC_ENTRY_ROLES_ENABLED

A restrição acima é o padrão (`false`) e cobre os dois pontos em que os papéis
iniciais são barrados:

| Operação | `false` (padrão) | `true` |
| --- | --- | --- |
| `junior` publicar criticidade alta | `403` | permitido |
| `junior` e `pleno` revisarem publicação | `403` | permitido, restrito ao próprio `domain_function` |

`junior` e `pleno` liberados herdam a mesma restrição de domínio do `senior`:
revisam somente publicações cuja `funcao` confiável coincide com a sua. Apenas
`admin` cruza domínios, e isso a flag não altera. Fora do domínio autorizado, o
Hub continua respondendo `404` em vez de `403`, para não confirmar a existência
do runbook.

A flag vale para o Hub e para o portal. No portal ela decide apenas se o botão de
edição aparece; a autorização é reavaliada pelo Hub em cada revisão, então um
portal configurado de forma divergente não concede nada. Mantenha o mesmo valor
nos dois para evitar um botão que leva a `403`.

`RBAC_ENTRY_ROLES_ENABLED` é a única forma de um `junior` publicar criticidade alta.
Ela não altera papéis acima nem substitui a revisão humana obrigatória: o
Markdown continua passando por Secret Scanner, DLP e validação de gramática.

Uma revisão nunca sobrescreve a publicação. O Hub cria outro Job e acrescenta ao
frontmatter server-side:

```yaml
runbook_raiz: "<id-da-publicacao-inicial>"
revisao: 2
substitui: "<id-da-versao-anterior>"
```

As mesmas regras valem no portal local e em `lucien runbook revise <uuid>`, que
atende os três provedores. O papel e o domínio são reavaliados pelo Hub tanto na
leitura do corpo quanto na gravação da revisão, então nenhum cliente contorna a
autorização baixando o Markdown por outro caminho.

## Funções de domínio configuráveis

`RUNBOOK_DOMAIN_FUNCTIONS` define quais funções existem na instalação. O padrão,
quando a variável não é declarada, é `acessos,servidores,redes,suporte` — a mesma
lista que antes estava fixa no código, para que uma instalação existente não perca
domínios ao atualizar.

A lista governa três caminhos, e é importante que seja a mesma nos três: o `-r` do
`lucien start`, a criação de usuários pelo admin e o enrollment de jump server. Se
um usuário pudesse ser criado em domínio fora dela, a publicação implícita dele
cairia num diretório que o administrador nunca declarou.

A palavra "role" aparece com dois sentidos no projeto, e vale separá-los. Em
`lucien start -r`, **role é a área** — o que esta lista configura e o que vira
diretório. Já em `RBAC_ENTRY_ROLES_ENABLED` e no tipo `RoleLevel`, "role" é o
**nível de permissão**. Nenhum dos dois é o cargo da pessoa: o Lucien não modela
cargo, e a relação entre o título de alguém e o nível dela no Hub é decisão da
organização.

Níveis de permissão não entram nessa lista. `junior`, `pleno`, `senior` e `admin`
continuam fixos no código porque cada um carrega regra própria de autorização —
junior não publica criticidade alta, senior revisa apenas o próprio domínio, admin
cruza domínios. Torná-los configuráveis exigiria descrever essas regras em
configuração, o que moveria decisão de segurança para fora do código revisado.

O script do jump server tem a lista própria e **precisa ser ajustado à mão**
quando `RUNBOOK_DOMAIN_FUNCTIONS` mudar; o Hub recusa o que não reconhecer, então
uma divergência aparece como erro de enrollment, não como acesso indevido.

## Nome do autor no runbook publicado

O frontmatter identifica o autor no formato misto
`U000004 - Operador Exemplo de Demonstracao Júnior`. O nome completo vem do campo
GECOS da conta POSIX, que o SSSD preenche a partir do LDAP — não há consulta
nova nem credencial nova envolvida.

O username permanece **no mesmo campo**, e de propósito: ele é a identidade que
auditoria e RBAC conhecem. Substituí-lo pelo nome completo tornaria o documento
mais legível ao custo de rastreabilidade, e um nome não é único.

Sem nome no LDAP — ou para usuário criado pelo admin — o campo mostra só o
username, como antes.

O recorte do GECOS acontece no script do jump server, que conhece o formato:
`Nome Completo,sala,telefone,telefone`. Enviar o campo inteiro colocaria
telefone e sala no runbook publicado. O Hub sanea de novo — colapsa espaços,
remove caracteres de controle e limita a 120 caracteres — mas não teria como
saber que o quarto campo era um telefone.

O nome é **apenas exibição**: nenhuma decisão de autorização o consulta. Ele
chega pelo payload do enrollment, e o Hub o trata como conteúdo publicado, não
como identidade.

Uma troca de nome no LDAP propaga sozinha: o enrollment roda a cada login no
jump e atualiza o campo.

## Um operador em mais de uma área

Um usuário tem uma **área primária** (`domain_function`) e, opcionalmente, **áreas
adicionais** concedidas pelo admin. A primária é o destino quando `lucien start`
roda sem `-r`, e é ela que aparece no frontmatter por padrão. O `-r` aceita
qualquer área que o usuário tenha.

```bash
lucien admin user update U000004 -r servidores,acessos
```

A primeira da lista vira a primária; as demais, adicionais. A lista **substitui**
o conjunto inteiro, não incrementa: revogar uma área é omiti-la de um novo
comando. Cada área passa pela mesma checagem contra `RUNBOOK_DOMAIN_FUNCTIONS` —
conceder uma área não declarada criaria um diretório que o administrador nunca
previu.

O princípio não mudou: área continua sendo escopo de autoridade, não preferência.
O que mudou é que a autorização pode cobrir mais de uma área. Quem não foi
autorizado segue recebendo `403` na publicação e `404` na revisão.

Revisão acompanha publicação. Quem publica em `acessos` também revisa runbooks de
`acessos`: as duas operações gravam no mesmo diretório e passam pelas mesmas
camadas do Hub. Restringir só a revisão criaria a assimetria de alguém criar um
runbook e depois não poder corrigi-lo.

`admin` continua cruzando qualquer área, independente do que tenha concedido a
si mesmo.

## Nível de permissão no jump server

O enrollment automático cria o usuário como **`pleno`**. Quem decide isso é o
Hub, não o script: `deploy/jump/lucien-jump-enroll.py` envia apenas
`username` e, quando o Hub pede, `domain_function` — nunca o nível. Se o script
escolhesse, qualquer um com a credencial técnica do jump server poderia se
auto-atribuir `admin`. O script apenas verifica o que voltou e recusa a
identidade se o nível não for `junior`, `pleno` ou `senior`.

`pleno` publica, inclusive criticidade alta, mas não revisa. Promover a `senior`
é uma decisão humana:

```bash
lucien admin user update U000004 --level senior
```

## Migração de schema

O Hub aplica as migrações pendentes ao subir. Não há mais lista para executar à
mão, nem ordem para lembrar: `backend/migrations/*.sql` são aplicadas em
sequência, cada uma na sua transação, sob trava de sessão do PostgreSQL para que
duas réplicas subindo juntas não apliquem a mesma duas vezes.

O que já rodou fica em `schema_migrations`:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec postgres psql -U lucien -d lucien -c 'TABLE schema_migrations'
```

A coluna `origem` diz como cada versão entrou:

| origem | significado |
| --- | --- |
| `aplicada` | o Hub executou o arquivo `.sql` |
| `adotada` | o efeito já estava no banco; nada foi executado |
| `modelo` | instalação nova, criada de uma vez pelo modelo |

`adotada` é o que acontece na primeira subida de uma instalação que aplicou os
arquivos à mão. Cada migração declara um marcador -- a coluna ou a tabela que
ela cria -- e o marcador, não o registro, é a autoridade. É isso que também
conserta uma queda entre executar a migração e registrá-la: na subida seguinte
o marcador responde que já está lá e a versão é quitada em vez de repetida.

Instalação nova não executa a `001`: ela pressupõe as tabelas legadas `users` e
`jobs` e renomeia colunas existentes. Num banco sem `users`, o modelo cria o
schema atual inteiro e as doze versões nascem quitadas como `modelo`.

### Antes de uma janela de manutenção

Faça a cópia de segurança e prove que ela restaura (`scripts/backup-db.sh` e
`scripts/test-restore.sh`). A `001` e a `003` adquirem `ACCESS EXCLUSIVE`
em `users` e `jobs` para instalar constraints sem aceitar estado parcial; em
banco com volume, estime a duração em homologação antes.

Usuários legados migram como `junior` por menor privilégio, e a `002` fecha o
latch de bootstrap se já existir qualquer admin. Depois da migração, quando
aplicável, abra uma janela curta de bootstrap para criar o primeiro admin.

Os arquivos continuam aplicáveis à mão com `psql -f`, onde o `BEGIN`/`COMMIT`
de cada um dá a atomicidade. Pela subida do Hub, quem controla a transação é o
runner, que inclui nela o registro da versão.
