# Hook `pre-receive` de secret scanning no Gitea

## Por que ele existe

O Hub aplica Gitleaks e DLP em seis pontos, mas todos dentro da sua própria
fronteira. Conteúdo que chega ao repositório por outro caminho — uma correção
feita à mão na interface do Gitea, um `git push` direto, um script de migração —
não passa por nenhuma dessas camadas.

Com `STORAGE_PROVIDER=gitea` não existe fluxo de revisão pelo Hub, então a
correção manual é o único jeito de ajustar um runbook publicado. Isso torna o
hook a **única** barreira nesse caminho, não uma redundância.

O hook falha fechado: sem Gitleaks disponível ou sem configuração legível, o
push é recusado.

## Quando este passo se aplica

É um passo **opcional e manual**, executado fora da stack do Lucien. Nada no
Compose depende dele, e o Hub funciona igual com ou sem o hook instalado.

| `STORAGE_PROVIDER` | Aplica-se? | Por quê |
| --- | --- | --- |
| `gitea` | **sim** | é onde falta: sem fluxo de revisão pelo Hub, a correção manual no repositório é o único caminho e não passa por nenhuma camada |
| `github` | **não** | o GitHub.com não oferece `pre-receive`; apenas o GitHub Enterprise Server aceita hooks server-side |
| `local` | não se aplica | não existe repositório Git nem push: o Hub grava direto no volume, e a revisão pelo portal já passa por Gitleaks e DLP |

No modo `local` a lacuna simplesmente não existe — `POST /runbooks/{id}/revisions`
é o caminho de correção e ele é auditado de ponta a ponta. O hook é a resposta
específica para o modo Gitea, onde esse fluxo está desabilitado.

Para `github`, as alternativas seriam push protection do GitHub Advanced
Security ou um check obrigatório de CI no Pull Request. Nenhuma das duas é um
gate no push como o `pre-receive`: a primeira depende de licença, a segunda roda
depois que o conteúdo já chegou ao repositório.

## Contexto de implantação

O Gitea roda em VM própria, instalado no sistema, e **não faz parte da stack do
Lucien** — o Hub apenas publica nele por HTTPS. Por isso o binário do Gitleaks
precisa existir naquela VM: o `pre-receive` executa dentro do processo que recebe
o push, e não há como delegá-lo a um contêiner da stack.

Duas alternativas foram descartadas de propósito:

- **chamar o `secret-scanner` do Lucien por HTTP** exigiria publicar um endpoint
  que hoje não tem autenticação nenhuma, e acoplaria a disponibilidade de push à
  do Hub; como o hook falha fechado, o Hub fora do ar impediria qualquer push;
- **`docker run` a partir do hook** exigiria dar ao processo do Gitea acesso ao
  socket Docker, o que equivale a root na VM.

## Pré-requisitos

- acesso `root` ou equivalente na VM do Gitea;
- nenhuma alteração no `app.ini` é necessária;
- arquitetura `linux/amd64` — confirme com `uname -m`.

## 1. Instalar o Gitleaks

O Hub e o repositório precisam rodar **o mesmo binário**. Versões diferentes
discordam sobre o que é segredo, e um valor aceito de um lado e recusado do outro
é pior do que não ter varredura.

### Recomendado: extrair da imagem já fixada por digest

Rode no host do Lucien, que já tem Docker e a imagem fixada:

```bash
docker create --name gitleaks-extract ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f && docker cp gitleaks-extract:/usr/bin/gitleaks /tmp/gitleaks && docker rm gitleaks-extract && sha256sum /tmp/gitleaks
```

Anote o SHA-256, transfira e confira do outro lado antes de instalar:

```bash
scp /tmp/gitleaks <usuario>@<vm-gitea>:/tmp/gitleaks
```

Na VM do Gitea:

```bash
sha256sum /tmp/gitleaks && sudo install -o root -g root -m 0755 /tmp/gitleaks /usr/local/bin/gitleaks && gitleaks version
```

O binário é Go estático; não depende de bibliotecas do sistema.

### Alternativa: release oficial, com verificação de integridade

Se preferir baixar direto na VM, **verifique o checksum**. Nunca instale a
ferramenta de segurança sem conferir o que foi baixado:

```bash
GITLEAKS_VERSION=8.30.1; BASE="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"; curl -fsSL -o /tmp/gitleaks.tar.gz "${BASE}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" && curl -fsSL -o /tmp/gitleaks_checksums.txt "${BASE}/gitleaks_${GITLEAKS_VERSION}_checksums.txt" && cd /tmp && grep "linux_x64.tar.gz" gitleaks_checksums.txt | sha256sum -c -
```

Só prossiga se a saída terminar em `OK`. Confirme o nome exato dos artefatos na
página da release antes de rodar, porque ele muda entre versões:

```bash
sudo tar -C /usr/local/bin -xzf /tmp/gitleaks.tar.gz gitleaks && sudo chmod 0755 /usr/local/bin/gitleaks && gitleaks version
```

## 1b. Instalar as regras

Copie o conjunto de regras do repositorio do Lucien para a VM do Gitea:

```bash
scp secret-scanner/gitleaks-lucien.toml <usuario>@<vm-gitea>:/tmp/gitleaks.toml
```

Na VM do Gitea:

```bash
sudo install -d -m 0755 /etc/lucien && sudo install -o root -g root -m 0644 /tmp/gitleaks.toml /etc/lucien/gitleaks.toml
```

Confirme que o Gitleaks aceita o arquivo antes de seguir:

```bash
printf 'snmp-server community S3cr3tRW RW\n' | gitleaks stdin --config=/etc/lucien/gitleaks.toml --no-banner --redact=100 --exit-code=23; echo "rc=$? (23 = detectou, como esperado)" # gitleaks:allow
```

## 2. Instalar o hook

Nao e preciso mexer no `app.ini`. O Gitea gera `hooks/pre-receive` em cada
repositorio e esse script ja delega para todo executavel em
`hooks/pre-receive.d/`, repassando o stdin do git e recusando o push se algum
deles sair diferente de zero:

```bash
for hook in ${GIT_DIR}/hooks/${hookname}.d/*; do
  test -x "${hook}" && test -f "${hook}" || continue
  echo "${data}" | "${hook}"
```

Esse diretorio e o ponto de extensao. **Nao edite `hooks/pre-receive`**: ele diz
`AUTO GENERATED BY GITEA, DO NOT MODIFY`, e por ele passam protecao de branch e
integracao com a API. Sobrescrever quebra essas funcoes e o arquivo e regenerado.

Localize o repositorio:

```bash
find /var/lib/gitea/data/gitea-repositories -maxdepth 2 -name 'runbooks.git'
```

Veja o que ja existe no diretorio, para nao colidir com hooks do proprio Gitea:

```bash
ls -la /var/lib/gitea/data/gitea-repositories/<owner>/runbooks.git/hooks/pre-receive.d/
```

Instale com dono e permissao corretos:

```bash
REPO=/var/lib/gitea/data/gitea-repositories/<owner>/runbooks.git; install -o git -g git -m 0755 deploy/gitea/pre-receive-gitleaks.sh "$REPO/hooks/pre-receive.d/gitleaks" && ls -l "$REPO/hooks/pre-receive.d/"
```

O arquivo precisa pertencer ao usuario que roda o Gitea e ter o bit de execucao:
o laco testa `-x` e `-f` e **pula em silencio** o que nao atender, sem erro algum.

O nome `gitleaks` faz o script rodar depois do hook interno do Gitea, que comeca
com `gitea`, porque o glob ordena alfabeticamente. Se precisar de outra ordem,
prefixe com numero (`50-gitleaks`).

### Sobre a aba Git Hooks da interface

Ela so aparece com `DISABLE_GIT_HOOKS = false` na secao `[security]` do
`app.ini` e sessao de administrador do site. **Nao recomendamos habilita-la**:
isso concede execucao de script arbitrario na VM a todo administrador do site,
de forma permanente. A instalacao em disco tem o mesmo efeito em runtime e nao
cria privilegio permanente.

## 3. Validar

Crie um commit com um segredo de teste em um clone descartável:

```bash
git clone https://gitea.exemplo.interno/infraestrutura/runbooks.git /tmp/teste-hook
cd /tmp/teste-hook
printf 'snmp-server community S3cr3tRW RW\n' > teste-hook.md # gitleaks:allow
git add teste-hook.md && git commit -m "teste do hook"
git push
```

O push deve ser **recusado**, com a mensagem `PUSH REJECTED — Gitleaks detected
a possible secret.` e sem exibir o valor do segredo, porque o hook usa
`--redact=100`.

Confirme depois que um commit legítimo passa:

```bash
git reset --hard HEAD~1
printf 'Procedimento sem segredo.\n' > teste-hook.md
git add teste-hook.md && git commit -m "teste sem segredo" && git push
```

Remova o arquivo de teste ao terminar.

## Ajustes

| Variável | Padrão | Uso |
| --- | --- | --- |
| `LUCIEN_GITLEAKS_BIN` | `/usr/local/bin/gitleaks` | caminho do binário |
| `LUCIEN_GITLEAKS_CONFIG` | `/etc/lucien/gitleaks.toml` | conjunto de regras |
| `LUCIEN_GITLEAKS_TIMEOUT` | `60` | limite por referência recebida, em segundos inteiros |

## Limitações

O hook varre os commits que chegam no push. Um segredo que já esteja na história
do repositório não é detectado por ele — para isso, rode uma varredura completa
uma vez:

```bash
gitleaks git --config=/etc/lucien/gitleaks.toml --redact=100 /caminho/do/clone
```

Um segredo já publicado precisa ser **rotacionado**, não apenas removido: o valor
permanece recuperável no histórico e em qualquer clone existente.

Mantenha `/etc/lucien/gitleaks.toml` sincronizado com
`secret-scanner/gitleaks-lucien.toml` do repositório do Lucien. Regras divergentes
fazem o Hub e o repositório discordarem sobre o que é segredo.
