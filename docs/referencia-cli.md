# Referência de comandos — `lucien-cli`

Guia de todos os comandos, argumentos e flags do CLI.

## `lucien auth`

Grupo sem ação própria — use um dos subcomandos.

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien auth status` | — | — | Valida o token salvo e mostra a identidade do Hub: usuário, ID, nível de permissão e todas as áreas autorizadas. Também aplica `LUCIEN_EXPECTED_USERNAME` se a variável estiver definida. |
| `lucien auth ensure` | — | — | Valida a credencial salva silenciosamente, ou dispara o fluxo interativo de login se não houver uma credencial válida. Pensado para uso não interativo/scripts. |

## `lucien admin`

Grupo de gestão de identidades no Hub — a autorização é sempre aplicada pelo Hub, não pelo cliente.

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien admin user create <name>` | `name` (obrigatório) | `--level <string>` (obrigatório) — nível de permissão: `junior`, `pleno`, `senior` ou `admin`.<br>`-r, --role <string>` — áreas separadas por vírgula; a primeira é a primária (padrão usado por `start` quando não se passa `-r`). | Cria um usuário e exibe **uma única vez** um token provisório de 4 horas. O novo usuário precisa rodar `lucien login` dentro desse prazo. |
| `lucien admin user issue-provisional-token <user-id-or-name>` (alias: `rotate-token`) | `user-id-or-name` (obrigatório) | — | Invalida o token permanente do usuário e emite um novo token provisório de 4 horas. |
| `lucien admin user update <user-id-or-name>` | `user-id-or-name` (obrigatório) | `--level <string>` — novo nível de permissão.<br>`-r, --role <string>` — áreas separadas por vírgula, **substituindo** o conjunto atual; a primeira é a primária. | Atualiza nível e/ou áreas de um usuário. Pelo menos uma das duas flags é obrigatória. Omitir `-r` preserva as áreas atuais; informá-la substitui o conjunto inteiro. |
| `lucien admin user revoke <user-id-or-name>` | `user-id-or-name` (obrigatório) | `--yes` (bool, padrão `false`, obrigatório) — confirma a revogação do usuário. | Revoga o token do usuário imediatamente. Sem `--yes` o comando recusa executar. |
| `lucien admin user reinstate <user-id-or-name>` | `user-id-or-name` (obrigatório) | `--yes` (bool, padrão `false`, obrigatório) — confirma a readmissão do usuário. | Revoga o token do usuário imediatamente. Sem `--yes` o comando recusa executar. |

## `lucien login`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien login` | — | `--token-stdin` (bool, padrão `false`) — lê o token via stdin, sem expor nos argumentos do processo.<br>`--quiet` (bool, padrão `false`) — suprime a saída de sucesso, para automação controlada. | Armazena um token recém-emitido do Hub para o usuário do sistema operacional atual. Aceita token provisório ou permanente; provisórios são trocados por um permanente e o perfil é salvo localmente. |

## `lucien create`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien create user <name>` | `name` (obrigatório) | — | Cria o **primeiro** administrador via bootstrap e ativa o perfil local. Requer a variável de ambiente `LUCIEN_BOOTSTRAP_KEY`. Uso único, para inicializar o primeiro admin — depois de rodar, não use `lucien login`. |

## `lucien reviews`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien reviews` | — | — | Lista os jobs do usuário ativo aguardando conclusão (ID, NAME, STATUS, CREATED AT). O índice numérico exibido pode ser usado como atalho `<review_index>` nos subcomandos de `job`. |

## `lucien job`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien job <id_or_name_or_review_index>` | 1 posicional (obrigatório): UUID, nome ou índice numérico da lista de `reviews` | `--reset` (bool, padrão `false`) — descarta o rascunho salvo e recomeça do template. | Seleciona comandos e abre o playbook no `$EDITOR`. Só funciona em jobs `PENDING`. Sem `--reset`, retoma um rascunho local existente. Abre um multi-select interativo dos comandos capturados, monta um template Markdown, abre o `$EDITOR` e salva o resultado como rascunho local (permissão 0600). |
| `lucien job cat <job_id>` | 1 posicional (obrigatório): **UUID exato** — nome/índice não funcionam aqui | — | Imprime o rascunho salvo sem abrir o editor. Lê só o rascunho local, nunca contata o Hub — útil quando um rascunho foi recusado na publicação. Recusa rodar dentro de uma sessão de gravação, para não vazar segredos no log gravado. Saída vai para stdout, pronta para pipe. |
| `lucien job sent <id_or_name_or_review_index>` | 1 posicional (obrigatório) | — | Publica o rascunho revisado. Carrega o rascunho local, calcula uma chave de idempotência (sha256 de usuário+job+conteúdo) e publica no Hub. Apaga o rascunho local em caso de sucesso; avisa se o Hub sanitizou (redigiu) valores sensíveis durante a publicação. |
| `lucien job del <id_or_name_or_review_index>` | 1 posicional (obrigatório) | `-y, --yes` (bool, padrão `false`) — pula a confirmação.<br>`-f, --force` (bool, padrão `false`) — cancela e apaga um job `PROCESSING`; nunca apaga jobs `PUBLISHED`. | Apaga um job `PENDING` ou `FAILED`; com `--force`, também cancela um `PROCESSING`. Pede confirmação a menos que `--yes` seja passado. Remove também qualquer rascunho local. |
| `lucien job status <id_or_name_or_review_index>` | 1 posicional (obrigatório) | — | Mostra o status de processamento assíncrono: ID, Name, Status e Error (se houver). Use para acompanhar depois de `upload` ou `retry`. |
| `lucien job retry <id_or_name_or_review_index>` | 1 posicional (obrigatório) | `-s, --skip-enrichment` (bool, padrão `false`) — reprocessa sem a etapa de enriquecimento por SLM; se omitida, mantém a escolha do upload original. | Reprocessa um job em `FAILED` (único status aceito). Reenfileira o job e informa o comando de status para acompanhar. |

## `lucien runbook`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien runbook revise <published_runbook_uuid>` | 1 posicional (obrigatório): **UUID exato** do runbook publicado — sem índice, sem nome | — | Revisa um runbook publicado, criando um sucessor imutável. Baixa o corpo publicado, abre no `$EDITOR`, envia o resultado de volta ao Hub, que cria uma nova versão imutável preservando a linhagem. Vira no-op ("No changes detected; revision cancelled.") se o conteúdo editado for igual ao original. Usa concorrência otimista via hash de conteúdo e chave de idempotência. |
| `lucien runbook cat <published_runbook_uuid>` | 1 posicional (obrigatório): **UUID exato** do runbook publicado — sem índice, sem nome | — | Imprime o corpo de um runbook publicado sem abrir o editor. Mesma relação que `job cat` tem com `job`: ler o que existe sem o risco de editar. Diferente de `job cat`, este consulta o Hub — um runbook publicado só existe lá, e já passou pela política de segredos e pelo DLP antes de ser publicado. Requer o UUID exato, como `revise`. Recusa rodar dentro de uma sessão de gravação, para não sujar a captura. Saída vai para stdout, pronta para pipe. |

## `lucien start`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien start <provider_name>` | `provider_name` (obrigatório) | `-d, --describe <string>` — descrição curta da tarefa (recomendada, melhora a precisão da SLM); máx. 280 caracteres, aparado; avisa se omitida.<br>`-r, --role <string>` — área para publicar (não é nível de permissão; padrão é a área do próprio usuário). Deve casar com `^[a-z][a-z0-9_]{2,63}$` quando informada.<br>`-y, --yes` (bool, padrão `false`) — descarta sem perguntar uma sessão parada e nunca enviada, para começar a nova captura. | Abre um PTY e grava stdin/stdout localmente. A sessão precisa ser finalizada depois com `stop` e então `upload`. Se já existir uma sessão parada e nunca enviada, avisa e pede confirmação antes de sobrescrevê-la (`lucien session cat`, `edit` ou `discard` resolvem sem perder a gravação; `--yes` pula a pergunta). Uma sessão ainda em `RUNNING` nunca é sobrescrita — `start` recusa até que `stop` a encerre. |

**Atenção:** `-r/--role` em `start` e em `admin user create/update` significa **área** (função/domínio, ex.: grupo de servidores ou de rede) — não confundir com nível de permissão. Nível de permissão é a flag separada `--level` em `admin user create/update` (`junior`, `pleno`, `senior`, `admin`).

## `lucien stop`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien stop` | — | — | Para o PTY e preserva a sessão localmente. Pode ser rodado dentro do terminal gravado (invocado automaticamente) ou a partir de outro terminal. Avisa se o log local atingiu o limite de tamanho e foi truncado. |

## `lucien upload`

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien upload` | — | `-s, --skip-enrichment` (bool, padrão `false`) — pula a etapa de enriquecimento por SLM; o rascunho mantém só os comandos extraídos. | Envia ao Hub a sessão parada pendente (log já sanitizado). Em caso de timeout, reconcilia buscando o job pelo nome gerado da sessão. Limpa os arquivos de sessão locais em caso de sucesso; informa `Job_ID` e status, com a dica do comando de acompanhamento `lucien job status <id>`. |

## `lucien session`

Uma sessão fica em disco desde `lucien stop` até o Hub aceitá-la. Quando o upload é recusado — pela política de segredos, por exemplo — ela permanece lá indefinidamente, porque a limpeza automática só roda depois de um upload aceito. Estes comandos servem para olhar a sessão, corrigi-la e descartá-la.

| Comando | Argumentos | Flags | Descrição |
| --- | --- | --- | --- |
| `lucien session cat` | — | — | Imprime a sessão gravada com as sequências ANSI removidas — byte a byte o que `lucien upload` enviaria ao Hub. É a gravação, não uma lista de comandos extraídos: separar comando de saída é trabalho do Hub, cuja gramática cobre tanto shells POSIX quanto CLIs de equipamento de rede — uma segunda gramática aqui divergiria dessa, e uma visão filtrada que discordasse do que o Hub lê de fato seria peor que nenhuma visão. Recusa rodar dentro de uma sessão de gravação, pela mesma razão de `lucien job cat`. Saída vai para stdout, pronta para pipe ou grep. |
| `lucien session edit` | — | — | Abre a sessão gravada no `$EDITOR` e salva de volta o que foi escrito; `lucien upload` lê o log do disco a cada tentativa, então o próximo envio manda o texto corrigido. É a saída para uma recusa que, de outra forma, custaria a gravação inteira. O que se edita é a gravação — a evidência de onde o runbook publicado é construído; remover um segredo é para isso que serve, reescrever a saída que o equipamento retornou frustra o propósito de gravar. O Hub varre de novo no próximo upload, então editar não passa o portão — só permite alcançar a etapa de revisão a que já se tinha direito. Recusa rodar dentro de uma sessão de gravação. |
| `lucien session discard` | — | `-y, --yes` (bool, padrão `false`) — pula a confirmação. | Remove definitivamente o estado da sessão e o arquivo de log desta máquina. Nada é enviado ao Hub — uma sessão recusada nunca criou um job lá, então não há o que apagar do outro lado. Use depois de um upload recusado pela política de segredos: a recusa mantém o segredo fora do Hub, mas a gravação que o contém permanece neste disco até ser removida. |

## Índice numérico de review

Vários subcomandos de `job` aceitam um `<review_index>` no lugar do UUID/nome: é o número mostrado por `lucien reviews`, resolvido internamente contra a lista de jobs ativos do usuário (1-based).
