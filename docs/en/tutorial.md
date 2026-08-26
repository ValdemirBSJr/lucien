# Usage tutorial

This tutorial runs the complete cycle: start the environment, create the initial
identity, record a session, review the commands, and publish the runbook.

## Prerequisites

- Docker Engine with Compose v2;
- the required local ports available;
- a terminal editor, such as `vi`, `vim`, or `nano`;
- access to the project repository;
- the native CLI for Linux or macOS; Windows is not supported for PTY capture;
- Docker Desktop integrated with Ubuntu/WSL in the central build environment;
- for a distributed environment, DNS and firewall allowing client → Hub/TCP 8443.

!!! danger "Before you start"
    Never use the `CHANGE_ME` values in a real environment. Do not put tokens,
    passwords, or keys in the task description, in the commands, or in the
    Markdown.

## 1. Prepare the consolidated environment

From the root of the Linux package, use the installer. It generates the
configuration, secrets with restricted permissions, an image tag derived from the
content, and certificates:

```bash
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

`.env` holds only non-sensitive options. The PostgreSQL password, the database
URL, the bootstrap key, the pepper, and the tokens live in `0444` files under the
`secrets/` directory with mode `0700`. The bootstrap key the CLI uses must be read
from `secrets/bootstrap_api_key` and transferred over a secure channel only for
the controlled run that creates the first admin.

For the first use, keep:

```dotenv
COMPOSE_PROFILES=consolidated
# Use localhost when the native CLI is on the same machine as the Hub.
API_HOST=https://localhost:8443
USER_CREATION_ENABLED=true
STORAGE_PROVIDER=local
```

`API_HOST` is the only address binding between the CLI and the Hub. Do not
hard-code a hostname or IP in the client.

## 2. Validate the TLS certificates

```bash
openssl verify -CAfile certs/ca.crt certs/server.crt
```

The installer already generates the set when it is missing, and rejects partial
sets. The certificate must contain every name used in `API_HOST`. Distribute only
`ca.crt` to the clients; the CA private key must not be mounted in the Hub or
copied to the CLI.

## 3. Start the services

If you answered "no" to the **Bring the Hub up now** prompt, run:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.build.yml build
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml logs -f slm-init
```

Wait for the model to download and be prepared. Check the Hub:

```powershell
docker compose ps
```

PostgreSQL and Ollama must stay on private networks, with no publicly exposed
ports.

## 4. Distribute and install the native CLI

The CLI must run directly in the operator's Linux or macOS terminal so the PTY
records the real session. The `lucien` container is not the capture path.

The operator should not compile the client. On a central build machine, generate
the Linux and macOS packages for `amd64` and `arm64`, with their checksums. The Go
compiler runs inside Docker:

```sh
chmod +x scripts/build-cli.sh
VERSION=1.2.3 ./scripts/build-cli.sh # replace with the approved version
```

The result lands in `dist/` and can be published to the company artifact
repository. Do not commit those binaries to the code repository. Each package
holds only `lucien` and short instructions; tokens and certificates stay separate.
The macOS packages are cross-compiled, but neither signed nor notarized. For a
corporate distribution without Gatekeeper warnings, sign with a Developer ID and
notarize on a protected macOS runner. Windows still has no PTY capture support.

On Linux, copy these four artifacts to the operator's host, or to the
administrative host used for the bootstrap:

- `deploy/install-cli.sh`;
- `lucien_<version>_linux_<architecture>.tar.gz`;
- the matching `.tar.gz.sha256` file;
- only the public `ca.crt` generated on the Hub.

Run the installer, which is separate from the Hub's:

```sh
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

The installer detects `amd64`/`arm64`, asks for the package path and for
`ca.crt`, validates the checksum, the architecture, and the CA extensions, and
then offers two scopes:

- current user: `~/.local/bin/lucien`, CA in `~/.local/share/lucien/`, and the
  environment in `~/.config/lucien/env`;
- system-wide: `/usr/local/bin/lucien`, CA in `/etc/lucien/`, and the environment
  in `/etc/profile.d/lucien.sh`, using `sudo` or running as `root`.

It also asks for `API_HOST` and `EDITOR`, can test `/health`, and can optionally
run the bootstrap of the first administrator. `LUCIEN_BOOTSTRAP_KEY` is read
without echo and used only in that run; it does not enter the environment file.
Completion is installed automatically for Bash, Zsh, or Fish; open a new terminal
to load it.

!!! warning "The CA belongs to the Hub"
    The CLI installer does not create certificates. Generate the CA and the
    certificate on the Hub, then copy only `certs/ca.crt`. A CA created on the
    client would not have signed the Hub certificate and therefore would not allow
    the TLS connection.

To install manually on Linux or macOS, detect the matching package, validate it,
and install the binary:

```sh
VERSION=1.2.3 # replace with the approved version you received
case "$(uname -s)" in Linux) sistema=linux ;; Darwin) sistema=darwin ;; *) exit 1 ;; esac
case "$(uname -m)" in x86_64) arquitetura=amd64 ;; arm64|aarch64) arquitetura=arm64 ;; *) exit 1 ;; esac
pacote="lucien_${VERSION}_${sistema}_${arquitetura}.tar.gz"

# Receive the package, its .sha256 file, and the public CA over a trusted channel.
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c "$pacote.sha256"
else
  shasum -a 256 -c "$pacote.sha256"
fi
tar -xzf "$pacote"
install -d -m 0755 "$HOME/.local/bin" "$HOME/.local/share/lucien"
install -m 0755 "lucien_${VERSION}_${sistema}_${arquitetura}/lucien" "$HOME/.local/bin/lucien"
install -m 0644 ca.crt "$HOME/.local/share/lucien/ca.crt"

# Makes the command available in this shell.
export PATH="$HOME/.local/bin:$PATH"
export API_HOST="https://localhost:8443"
export TLS_CA_FILE="$HOME/.local/share/lucien/ca.crt"
export EDITOR="vi"
command -v lucien
lucien help
```

In a distributed environment, replace `localhost` with the Hub FQDN and install
only `ca.crt` in a protected path on the client. The binary does not load `.env`
on its own: the installation mechanism must inject those variables into the
environment.

To keep `PATH` in new terminals, use `~/.profile` on Linux/bash or `~/.zprofile`
on macOS/zsh:

```sh
perfil="$HOME/.profile"
[ "$(uname -s)" = "Darwin" ] && perfil="$HOME/.zprofile"
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$perfil" 2>/dev/null || \
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$perfil"
```

In a managed corporate installation, the platform team can install `lucien` into
`/usr/local/bin` and the CA into `/etc/lucien/ca.crt`. The operator should not use
`sudo` to create an installation of their own. Never copy `ca.key`, `server.key`,
or `server.crt` to the client.

## 5. Create the first administrator

Run the bootstrap only on the controlled host that holds the key. It must not
enter the operators' permanent environment.

If you chose to create the administrator inside `deploy/install-cli.sh`, skip the
manual command below and go straight to closing the bootstrap. The CLI has already
saved the username in the local profile and the token in the keyring or, if
authorized, in a file with permission `0600`. The permanent credential is also
displayed once, for storage in the administrative vault.

The CLI is native and can run directly in WSL, without entering Compose. For a
test in the same WSL as the Hub, use the server's real IP and copy only `ca.crt`
into the CLI environment. Do not `source .env`: that file holds Hub secrets.

```sh
export API_HOST=https://10.0.0.1:8443
export TLS_CA_FILE=/secure/path/ca.crt
```

Replace `10.0.0.1` with the IP given to the installer. If the CLI is on another
machine, copy only `certs/ca.crt`; never copy `ca.key`, `server.key`, or the Hub's
`.env`.

```sh
read -r -s -p "LUCIEN_BOOTSTRAP_KEY: " LUCIEN_BOOTSTRAP_KEY; echo
export LUCIEN_BOOTSTRAP_KEY
lucien create user administrator
unset LUCIEN_BOOTSTRAP_KEY
```

The Hub refuses a new bootstrap once an administrator exists, even if someone
retains the variable. That guarantee is transactional in the database and holds
across workers and replicas; the bootstrap does not reopen after a revocation.
Even so, retaining the secret on the client is an operational failure and must not
be accepted.

After success:

1. set `USER_CREATION_ENABLED=false`;
2. keep `BOOTSTRAP_API_KEY` exclusively in a server-side secret;
3. recreate only the Hub to apply the closing of the window.

```powershell
docker compose --profile consolidated up -d --force-recreate hub
```

In WSL, using the local Compose created by the installer, the equivalent is:

```sh
sed -i 's/^USER_CREATION_ENABLED=.*/USER_CREATION_ENABLED=false/' .env
docker compose --env-file .env -f docker-compose.local.yml \
  --profile consolidated up -d --force-recreate hub
```

If you chose the `server` profile, replace `consolidated` with `server`.

Only after this step move on to Gitea Actions. `act_runner` with Docker access is
equivalent to `root`; in production it must live on a dedicated host, separate
from the Hub, the database, the SLM, and Gitea. The guided Hub installation
rejects runner mode when it detects a Hub configuration in the same directory.

The `create user` command is not a general registration: it creates exclusively
the first admin. Later users are created by an administrator through
`POST /admin/users`.

## 6. Log in

If the installer or `lucien create user` has just created the first
administrator, do not run `login`: the token was already saved automatically.
Confirm with `lucien auth status`.

To register the remaining users, the administrator runs:

```bash
lucien admin user create operador \
  --role junior \
  --domain servidores
```

The provisional credential is shown exactly once, expires in four hours, and must
be delivered through a vault or secure channel. The user installs the CLI and the
public CA, runs `lucien login`, and pastes the credential into the prompt without
echo. The Hub consumes it, issues the permanent one, and the CLI shows it once and
saves it locally. Confirm with `lucien auth status`.

If the last administrator loses their credential, recover it on the Hub host
without reopening the bootstrap and without erasing data:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec hub python -m app.recover_admin Admin
```

Always use the prompt without echo; the CLI rejects a token passed as an argument,
to avoid shell history and exposure in the process list:

```powershell
lucien login
```

The CLI validates the permanent credential at `/me` and stores it in the available
keyring. On a controlled Unix host, the file fallback requires
`LUCIEN_ALLOW_FILE_TOKEN=true` and uses permission `0600`.

If the permanent credential is lost, the admin runs:

```bash
lucien admin user issue-provisional-token operador
```

The new provisional credential immediately invalidates the previous permanent one
and any pending provisional one. The user repeats `lucien login` within four
hours.

### Jump server

The administrator installs the integration on the host exactly once. First, issue
the minimal-scope credential on the Hub:

```bash
docker compose --env-file .env \
  -f docker-compose.local.yml \
  exec -T hub python -m app.issue_jump_enrollment_key

sudo ./deploy/install-jump-server.sh
```

The second command asks for the `luc_jump_...` credential without echo. On every
interactive SSH login, the LDAP ID is compared with the Hub username. A new user
picks their area and is created as `pleno`; existing `junior`, `pleno`, or
`senior` users keep their role and domain. `admin` accounts use the administrative
login. Tokens travel over `stdin`, never through arguments or `.bashrc`. If
authentication fails, the CLI's protected operations are blocked, but
`lucien stop` stays available so a local session is preserved.

Do not use this mode with a shared Unix account. The complete administrative
procedure is in
[Installation manual](manual-instalacao.md#usar-em-jump-server).

## 7. Record a task

In the first terminal, start the capture:

```powershell
lucien start redis-cache -d "Update Redis and validate replication"
```

`-d` or `--describe` is optional, accepts up to 280 characters, and is recommended
to improve the SLM's disambiguation. The description is sanitized and grants no
privilege.

Run the maintenance commands normally inside the shell that opens. Do not paste
credentials. In that same recorded shell, end the PTY and preserve the local
session:

```powershell
lucien stop
```

The command ends the child shell and returns the original terminal; do not run
`exit` before it. If you prefer, `stop` can also be called from a second terminal
under the same account. Typing just `exit` is a valid alternative and leaves the
session ready for `upload`. Since there has been no communication with the Hub
yet, `stop` cannot display a `Job_ID`; its output tells you to run
`lucien upload`, which returns the ID and the follow-up command after acceptance.

`stop` consults neither a token nor the Hub. To sanitize and send the finished
session:

```powershell
lucien upload
```

The `upload` output holds the `Job_ID` and `Status: PROCESSING`; it does not wait
for the SLM. Check progress with:

```powershell
lucien job status <JOB_ID>
```

At `PENDING`, continue with `reviews` and editing. At `FAILED`, fix the health of
the SLM or the scanner and run `lucien job retry <JOB_ID>`. If authentication, the
network, or the Hub fails before acceptance, the state and the log stay local;
repeat only `upload`. The CLI and the Hub refuse `retry` while the job is
`PROCESSING`. That does not block uploading another session: every newly accepted
capture creates its own job in the queue. The CLI reconciles a lost response by
name before removing the files. While a session is waiting for upload, a new
`start` is refused, to avoid overwriting and losing the audit trail.

## 8. Follow the job queue

```powershell
lucien reviews
```

The table shows the active jobs belonging to the authenticated user:

- `PROCESSING`: the worker is still processing the session;
- `PENDING`: the commands are ready for review;
- `FAILED`: processing failed and can be requeued after diagnosis.

`PUBLISHED` jobs do not appear in that queue. To look up a specific job, use
`lucien job status <JOB_ID>`. Another user cannot look up, edit, publish, or delete
those jobs.

Every command that selects a job (`job`, `status`, `retry`, `sent`, and `del`)
also accepts the 1-based position shown by `lucien reviews`. Since the queue is
dynamic, run `reviews` again before publishing or deleting by index.

If a processing run must be abandoned, cancel it explicitly:

```bash
lucien job del <JOB_ID> --force
```

The Hub atomically removes the job and the encrypted payload from the queue. The
flag does not allow deleting completed publications and does not bypass
per-owner isolation.

On Linux, to refresh the table automatically every five seconds:

```bash
watch -n 5 lucien reviews
```

## 9. Review and write the runbook

```powershell
lucien job <id_or_name_or_index>
```

The index is the 1-based position shown by the last `lucien reviews`:
`lucien job 1` opens the first job in the table, and `lucien job 2` the second.
The table stays unchanged, and the CLI queries the same list again before
resolving the index. If the queue changes between commands, run `lucien reviews`
again before using a position.

The CLI presents the extracted commands. Check only the useful ones. Then it opens
the editor configured in `EDITOR` and creates a template with goal, prerequisites,
procedure, validation, and rollback. The template language comes from the Hub:
`SLM_LANGUAGE_RUNBOOK=pt-br` generates the whole skeleton and requests the SLM
suggestions in Brazilian Portuguese; `en` does the same in English. The CLI cannot
override that policy locally.

The SLM tries to fill in the goal, architecture/prerequisites, possible impacts,
and rollback commands. Every suggested passage is marked as **MANDATORY REVIEW**.
Lucien does not execute commands. Treat the content as an untrusted starting point
and validate target, impact, permissions, and recovery before publishing. Each
selected command carries its real, sanitized output along with it. The first five
lines are kept; larger outputs also show `...` and the last line. Unchecking a
command removes the command, its output, and its impact from the draft.

Every step must keep the heading immediately followed by the `bash` block. With
`SLM_LANGUAGE_RUNBOOK=en`, the CLI emits:

````markdown
> **MANDATORY REVIEW — CAPTURED COMMAND:** do not execute before validating the target, impact, permissions, and recovery plan.

> **MANDATORY REVIEW — SLM SUGGESTION:** possible impact: it reads the replication state, with no change expected. Validate before execution.

### Step 1: Check replication
```bash
redis-cli info replication
```
```text
role:slave
master_link_status:up
```
````

With `pt-br`, the same structure comes out with `### Passo 1:` and the
`REVISÃO OBRIGATÓRIA` notices. The Hub accepts both.

Do not add YAML frontmatter: author, role, function, date, and tags are injected
exclusively by the Hub. When you close the editor, the draft is stored locally
with restricted permissions.

The title removes only the technical suffix Lucien created. On publication, the
file combines that readable name with the full UUID, for example
`teste-uso_1--b8b6e6a1-5bd9-47cc-8a50-df1bea1a4055.md`. The provider never
overwrites divergent content. GitHub, Gitea, and the local page use the same
hierarchy by trusted domain and year, for example:
`docs/runbooks/servidores/2026/teste-uso_1--b8b6e6a1-5bd9-47cc-8a50-df1bea1a4055.md`.

## 10. Publish

```powershell
lucien job sent <id_or_name_or_index>
```

The command derives an idempotency key from the user, the job, and the content.
Repeating the publication after a timeout does not create another document when
the content is identical. If the publication destination fails, edit the draft
freely and send it again: while the job is `PENDING`, the new attempt replaces the
previous reservation using the new key derived from the content. Reusing a key
with different content returns `409`; after `PUBLISHED`, the content is immutable.
The Hub still runs these barriers:

1. authentication and ownership verification;
2. mandatory secret scanning; detection or unavailability blocks the action;
3. final Markdown sanitization by the DLP;
4. validation of the step grammar;
5. criticality RBAC;
6. server-side generation of the frontmatter;
7. publication through the configured provider.

If the Hub replaces sensitive data, the CLI reports only the number of
substitutions, never the values it found.

## 11. Purge or cancel a job

```powershell
lucien job del <id_or_name_or_index>
```

Use `--yes` only in controlled automation:

```powershell
lucien job del <id_or_name_or_index> --yes
```

To abandon a run in progress:

```powershell
lucien job del <id_or_name_or_index> --force
```

Only your own jobs can be affected. Without `--force`, only `PENDING` and `FAILED`
are deleted. Publications are immutable even with `--force`.

## Distributed environment

On the server:

```powershell
Copy-Item .env.server.example .env
docker compose --profile tools run --rm certgen
docker compose -f docker-compose.yml -f docker-compose.build.yml `
  --profile server build
docker compose --profile server up -d
```

On the client:

```powershell
export API_HOST="https://runbook.example.internal:8443"
export TLS_CA_FILE="/etc/lucien/ca.crt"
lucien login
```

Before starting, set `API_HOST` to the Hub FQDN, include the same FQDN in
`CERT_DNS`, and install the CA in the client volume. Open only TCP 8443 between
the necessary origins.

## Choosing the destination

The recommended path is to answer `deploy/install-hub.sh`, which generates a
single coherent preset. The four choices are:

| Choice | When to use it | Additional executor |
| --- | --- | --- |
| local + portal | runbooks stay on the Hub host | none; HTTPS/9091 portal |
| GitHub | documentation on GitHub Pages | GitHub-hosted runner |
| Compact Gitea | practical installation on the Hub host | fixed builder, no Docker socket |
| Gitea runner | organization with a dedicated CI VM | `act_runner` on the dedicated VM |

For local disk with a portal and controlled review:

```dotenv
COMPOSE_PROFILES=consolidated,local-viewer
STORAGE_PROVIDER=local
LOCAL_STORAGE_ROOT=/data/playbooks
VIEWER_BIND_ADDRESS=0.0.0.0
```

The session secret is generated in `secrets/viewer_session_secret`, never in
`.env`.

Open `https://<Hub-FQDN>:9091`. Provide your username and personal token; the name
alone does not authenticate. The page uses the distribution's `logo-lucien.png`,
groups runbooks by function/tags, and allows switching between light and dark
themes. Open TCP/9091 only to reader networks.

Every active Lucien user can view the catalog. The **Edit** option appears only for
`admin` and, within their own domain, `senior`. Edit only the Markdown body and
keep the `### Step`/`bash` block grammar; the portal sends no frontmatter. On save,
the Hub authenticates again, applies DLP, secret scanning, and RBAC, and creates a
new immutable revision. The previous version stays on disk and in the audit trail.

If the page reports that the version changed, reload before reapplying your
change. On a temporary unavailability of the destination, use **Try again** on the
same screen to preserve the content and the idempotency key; do not open another
tab.

For GitHub:

```dotenv
STORAGE_PROVIDER=github
GIT_API_BASE=https://api.github.com
GIT_OWNER=your-organization
GIT_REPO=runbooks
GIT_BRANCH=main
GIT_DOCS_PREFIX=docs/runbooks
```

Select **GitHub Actions** as the Pages source. Do not install a self-hosted runner
for this mode. On GitHub.com, a private repository is accepted on the Pro, Team,
and Enterprise Cloud plans, but the site can only have private access when it
belongs to a GitHub Enterprise Cloud organization. Confirm
**Settings → Pages → Visibility → Private** before publishing internal
documentation; if that option does not exist, the site must be treated as public.
Workflows in private repositories consume the plan's minute allowance and may
incur charges past the limit. The workflow shipped here does not support GitHub
Enterprise Server.

The Pages deploy uses only the temporary `GITHUB_TOKEN`. For the Hub to publish to
the private repository, use another *fine-grained* token, restricted to the
repository and with **Contents: Read and write**, in `GIT_TOKEN`; do not grant
Actions or Pages to that token. The workflow validates Pull Requests and publishes
only from `main`.

For compact Gitea, in addition to the provider variables, configure:

```dotenv
COMPOSE_PROFILES=consolidated,gitea-compact
STORAGE_PROVIDER=gitea
GIT_API_BASE=https://gitea.example.internal/api/v1
GIT_CA_SOURCE=./certs/gitea-ca.crt
WIKI_REPOSITORY_URL=https://gitea.example.internal/infrastructure/runbooks.git
WIKI_REPOSITORY_BRANCH=main
WIKI_REPOSITORY_USER=lucien-wiki-reader
WIKI_BIND_ADDRESS=127.0.0.1
```

The read-only token lives in `secrets/wiki_repository_token`. The Hub's write
token lives separately in `secrets/git_token`.

The Hub's `GIT_TOKEN` is still the write credential; do not reuse it in the
builder. Compact mode does not use Gitea Actions. For remote access to the wiki on
9092, configure an HTTPS proxy in front of the local bind.

For the Gitea runner, use the same provider, choose the fourth preset, and only
then run `--configure-gitea-runner` on the dedicated VM. Inject every token
through a secrets manager, never in a committed file.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `API_HOST não configurada` | confirm that the correct `.env` is loaded |
| CA or hostname error | check `TLS_CA_FILE`, `CERT_DNS`, and the hostname in `API_HOST` |
| `401` | invalid or revoked token, or the bootstrap is already closed |
| `403` on publication | job ownership or RBAC policy not satisfied |
| `403` when reviewing in the portal | only an admin or a domain senior can create a revision; the interface revalidates this with the Hub |
| `404` on the revision API | nonexistent ID, or a runbook outside the senior's domain; the Hub does not distinguish the two cases |
| `412` when reviewing in the portal | another revision changed the base; reload before editing |
| `409` when reviewing in the portal | the base already has a successor, or the key does not match the attempt; repeat the original form or reload |
| `422` with the secrets policy | remove the real credential; use a placeholder such as `SUA_SENHA_AQUI` |
| `502` with the secret scanner unavailable | restore the service; the Hub blocks for safety |
| no command detected | provide `-d`, reduce terminal noise, and confirm the SLM's health |
| truncated-log warning at `stop`/`upload` | the session exceeded `MAX_LOG_BYTES`; commands from the end may be missing — record shorter sessions or raise the limit |
| the editor does not open | set `EDITOR=vi`, `vim`, or another available executable |
| publication returns a conflict | the job is already `PUBLISHED`, the same key was reused with different content, or the destination already holds a divergent artifact |

For security details and limitations, continue in
[Technical documentation](documentacao-tecnica.md).
