# Lucien installation manual

This manual describes a distributed installation with:

- Hub, PostgreSQL, Secret Scanner, and the Ollama SLM on the same Linux server;
- Gitea on an external server;
- the Lucien CLI running directly in the operators' terminal;
- runbooks published to a Gitea repository.

The examples use reserved names. Replace `<HOST_DO_HUB>`, `<REDE_AUTORIZADA>`,
and the other values with your real environment. Do not publish IPs, tokens, or
internal names in the documentation of a public repository.

!!! danger "Do not mix files from different versions"
    `deploy/install-hub.sh`, `docker-compose.yml`, `backend/`, and the other
    directories must come from the same Lucien release. The current installer
    presents four publication modes. If the screen shows only three destinations,
    update the complete package before a new installation; swapping just the
    script can produce an incompatible Compose file.

## 1. Choose the publication mode

The current installer offers:

| Option | Use | Additional requirements on the Hub host |
| --- | --- | --- |
| `1) local-viewer` | local disk and HTTPS/9091 portal | `runbook-viewer/` and `logo-lucien.png` |
| `2) github` | GitHub-hosted Actions and GitHub Pages | no local runner |
| `3) gitea-compact` | fixed builder and Nginx on the Hub host | `wiki-builder/` and `deploy/nginx/wiki-compact.conf` |
| `4) gitea-runner` | Gitea Actions on a dedicated runner | workflow in the repository and another host for the runner |

For external Gitea with Actions, select `4) gitea-runner`. The minimum structure
of the Hub server is:

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

In compact mode, the one-shot service `wiki-volume-init` must show up as
`Exited (0)`. It prepares the volumes for the non-root builder; that state is
success, just as it is for `slm-init` after downloading the model.

`docker-compose.local.yml` and `.env` will be created by the installer. The
`certs-invalidos/` directory takes no part in execution and must not stay on the
server: it holds old private keys and needs to be archived in an offline vault or
destroyed according to corporate policy.

## 2. Server prerequisites

Use a dedicated Linux host or a VM with:

- Docker Engine and Docker Compose v2;
- OpenSSL and `coreutils`;
- HTTPS egress to pull images, the Ollama model, and to reach Gitea;
- persistent space for PostgreSQL and Ollama;
- TCP/8443 reachable exclusively from the client networks.

Confirm the requirements:

```bash
docker version
docker compose version
openssl version
realpath --version
```

If the Hub is published on an Internet IP, restrict TCP/8443 with a firewall, a
VPN, or a private network. TLS and the API token are mandatory, but they do not
justify leaving the port open to `0.0.0.0/0`. PostgreSQL, Ollama, and the Secret
Scanner must never have published ports.

## 3. Run the Hub installer

From the root of a complete and coherent copy of the package:

```bash
cd /opt/lucien-hub
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

Use this reference to answer the prompts:

| Prompt | Recommended answer | Explanation |
| --- | --- | --- |
| FQDN clients will use | `hub.example.internal` | The name present in `API_HOST` and in the certificate. Prefer DNS over an IP. |
| Expose HTTPS on TCP/8443 | `y` only for a remote CLI | Binds on every interface; the firewall must still limit the origins. |
| Additional SAN IP | the IP the CLI will use to reach the Hub | Needed when the client uses an IP address directly. |
| Run Ollama on this same machine | `y` | Selects the `consolidated` profile. |
| SLM model | `qwen2.5-coder:3b` or an approved model | The first use downloads the model and can take a while. |
| Runbook language | `pt-br` or `en` | Sets the template the Hub hands the CLI and the language of the inferred tags. It does not change the language of this documentation. |
| Publication mode | `4` for Gitea Actions | Use `3` only for the compact builder on the host itself. |
| Gitea API base | `https://gitea.example.internal/api/v1` | Use a single slash before `api/v1`. |
| Organization/owner | the repository's exact owner | The value can be case-sensitive depending on the provider. |
| Repository | name of the runbooks repository | Use a dedicated repository. |
| Branch | `main` | Must be the same branch the workflow watches. |
| MkDocs directory | `docs/runbooks` | Keeps the documents inside the compiled tree. |
| Git token | service token restricted to the repository | Grant only content read/write; never administrative privileges. |
| Open the bootstrap | `y` only on the first installation | The window must be closed right after creating the first administrator. |
| Bring the Hub up | `y` | Builds and starts the selected services. |

The confirmation prompts accept `y`, `Y`, `yes`, `YES`, or `Yes`. Anything else,
including an empty answer, means no.

The installer queries the number of CPUs the Docker daemon actually makes
available and does not generate limits above it. That matters especially on
Docker Desktop, whose VM may have fewer CPUs than the host operating system.

The installer does not ask whether it should generate TLS. When `ca.crt`,
`server.crt`, and `server.key` are missing, it runs `certgen` automatically. When
all three exist, it reuses the set without rotating it. A partial set is rejected,
to prevent certificates and keys from different issuances being mixed.

If there is no DNS and the CLI uses an IP, give that IP as the Hub address and
repeat it in the additional-SAN prompt. For permanent installations, internal DNS
and a certificate issued for that name are preferable.

### The `master` branch

The shipped workflow watches `main`. If the Gitea repository uses `master`, pick
one of these alternatives before publishing:

1. migrate the default branch to `main` and keep the shipped workflow; or
2. set `GIT_BRANCH=master` and also change the workflow trigger:

```yaml
on:
  push:
    branches:
      - master
```

Configuring the Hub for `master` and leaving the workflow on `main` makes the file
reach the repository, but it does not trigger the wiki build.

## 4. Check the generated files

The installer creates:

```text
.env                         # non-sensitive configuration; mode 0600
docker-compose.local.yml     # editable operational Compose
secrets/                     # server-side secrets; directory 0700
├── postgres_password        # individual files, mode 0444
├── database_url
├── bootstrap_api_key
├── auth_pepper
├── git_token
├── viewer_session_secret
└── wiki_repository_token
certs/ca.crt                 # public CA distributed to the clients
certs/ca.key                 # CA private key; remove from the host after backup
certs/server.crt             # Hub certificate
certs/server.key             # private key used only by the Hub
```

Fix any URL with a doubled slash. For example:

```dotenv
GIT_API_BASE=https://gitea.example.internal/api/v1
```

Check only the non-secret variables:

```bash
grep -E '^(COMPOSE_PROFILES|API_HOST|HUB_BIND_ADDRESS|SLM_BASE_URL|SLM_MODEL|SLM_LANGUAGE_RUNBOOK|STORAGE_PROVIDER|GIT_API_BASE|GIT_OWNER|GIT_REPO|GIT_BRANCH|GIT_DOCS_PREFIX)=' .env
```

`.env` no longer holds credentials. Even so, do not dump the configuration into
public logs: URLs and internal names can be sensitive too. Never run
`cat secrets/*`; root access or access to the Docker socket is still equivalent to
access to the mounted secrets.

### Adjusting an installation that already exists

!!! warning "Do not swap only the Compose file of a legacy installation"
    If `.env` still contains `POSTGRES_PASSWORD`, `DATABASE_URL`,
    `BOOTSTRAP_API_KEY`, `AUTH_PEPPER`, or tokens, it belongs to the old contract.
    The new Compose expects files in `secrets/` and images already built with
    `LUCIEN_IMAGE_TAG`. Do a controlled migration or, when there is no data to
    preserve, a clean installation with the complete package. Mixing the two
    formats stops PostgreSQL and the Hub from starting.

If `.env`, `docker-compose.local.yml`, and valid certificates already exist, do
not run the installer again: it refuses to overwrite those files. Open `.env` with
an administrative editor and:

1. replace `https://gitea.example.internal//api/v1` with
   `https://gitea.example.internal/api/v1`;
2. decide between `main` and `master` and use the same value in `GIT_BRANCH` and
   in the Gitea workflow;
3. preserve `secrets/` and its `0700/0444` modes, unless a rotation is planned.

When updating to the asynchronous upload, copy the new `backend/`,
`docker-compose.yml`, and `docker-compose.build.yml`. The active file is
`docker-compose.local.yml`. After copying the new files, sync it with the current
base:

```bash
./deploy/install-hub.sh --refresh-compose
```

The command validates that the base contains `upload-worker` and that every
service has CPU and memory limits and reservations. If a different local Compose
already exists, it is preserved as `docker-compose.local.yml.bak.*` before the
replacement. Review and reapply to the new file only the customizations that are
really necessary; prefer keeping operational adjustments in `.env`. Without
`upload-worker`, jobs stay in `PROCESSING` indefinitely.

In `.env`, add or adjust:

```dotenv
SLM_LANGUAGE_RUNBOOK=pt-br
SLM_TIMEOUT_SECONDS=300
UPLOAD_WORKER_POLL_SECONDS=2
UPLOAD_WORKER_LEASE_SECONDS=900
UPLOAD_WORKER_RETRY_BASE_SECONDS=10
UPLOAD_WORKER_MAX_ATTEMPTS=5
```

`SLM_LANGUAGE_RUNBOOK` accepts only `pt-br` or `en`. The change affects new drafts
opened by the CLI, plus the tags, goal, architecture/prerequisites, impacts, and
rollback suggested by the SLM; it does not translate already published documents,
nor the text the operator wrote by hand. It has no relation to the language of
this documentation, which is chosen by the selector in the page header.

`UPLOAD_WORKER_LEASE_SECONDS` must be at least twice the SLM timeout plus 30
seconds. Set a new immutable tag in `LUCIEN_IMAGE_TAG`. That tag is shared by
every locally built image, so build all the images of the active profiles, not
only the Hub:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  -f docker-compose.build.yml build
```

`upload-worker` reuses the Hub image and has no separate build. If only `hub` is
built after the tag changes, Compose will try to pull `secret-scanner`,
`wiki-builder`, or another local service from a registry and will fail with
`pull access denied`.

When updating an installation that already has a database, also copy
`backend/migrations/007_command_outputs_postgresql.sql`, stop the consumers, and
apply the migration once:

```bash
docker compose --env-file .env -f docker-compose.local.yml stop hub upload-worker
docker compose --env-file .env -f docker-compose.local.yml exec -T postgres \
  sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backend/migrations/007_command_outputs_postgresql.sql
```

Then recreate the API and the worker with the same versioned image:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  up -d --force-recreate hub upload-worker
```

On a new database, `create_all()` already creates the columns and migration `007`
is unnecessary. Do not generate certificates again because of this fix. The
current certificate stays valid as long as the SAN, the validity period, and the
private key are correct.

Protect the files:

```bash
chmod 0600 .env certs/ca.key certs/server.key
chmod 0444 secrets/*
chmod 0700 secrets
chmod 0644 certs/ca.crt certs/server.crt
chown 10001:10001 certs/server.key certs/server.crt
```

After storing `ca.key` on two controlled offline media and testing that the backup
reads back, remove the copy from the Hub host. Do not remove the only copy: it is
needed for a controlled reissue by the same CA.

## 5. Validate the Hub

Check the containers and the API logs:

```bash
docker compose --env-file .env -f docker-compose.local.yml ps
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 hub
```

Validate the certificate and the endpoint without disabling TLS:

```bash
openssl x509 -in certs/server.crt -noout -subject -issuer -ext subjectAltName

curl --fail --show-error \
  --cacert certs/ca.crt \
  https://<HOST_DO_HUB>:8443/health
```

Expected result:

```json
{"status":"ok"}
```

Do not use `curl -k`. If validation fails, fix the CA, the SAN, the clock, or the
hostname; disabling verification would hide the problem.

## 6. Prepare the CLI machine

The CLI is a native binary. Docker is not needed on the operator's machine.
Transfer over a trusted channel only:

- `deploy/install-cli.sh`;
- `deploy/install-jump-user.sh`, only for the manual per-account mode;
- `deploy/install-jump-server.sh` and `deploy/jump/`, only for the automated jump
  server mode;
- `lucien_<version>_linux_<amd64|arm64>.tar.gz`;
- the matching `.tar.gz.sha256` file;
- the public `ca.crt` copied from the Hub.

Never transfer `.env`, `ca.key`, `server.key`, or the Git token.

Run:

```bash
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

The installer validates the checksum, the architecture, and the CA extensions.
Then it asks for:

| Field | Example |
| --- | --- |
| Scope | current user (`~/.local/bin`) or system-wide (`/usr/local/bin`) |
| Package | `/tmp/lucien_1.2.3_linux_amd64.tar.gz` |
| Public CA | `/tmp/ca.crt` |
| Hub URL | `https://<HOST_DO_HUB>:8443` |
| Editor | `vi`, `vim`, or another trusted editor |

The script can validate `/health` and create the first administrator. The
bootstrap key is read without echo and used in memory only. It is not saved into
the CLI environment file. Completion is installed automatically for the Bash, Zsh,
or Fish login shell. In system-wide installations, all three formats are installed
into the conventional directories under `/usr/local/share`.

To load the configuration immediately, run the command the installer prints. In a
per-user installation, that is usually:

```bash
. "$HOME/.config/lucien/env"
lucien help
```

## 7. Create the first administrator

If that option was accepted in the CLI installer, provide a username and the
`BOOTSTRAP_API_KEY` stored on the server in `secrets/bootstrap_api_key`. Read it
only in an administrative terminal that is not being recorded, and transfer it to
the operator through a secure channel or a temporary vault. Do not pass the key as
an argument, do not put it in chat, and do not leave it in the permanent
environment.

The equivalent manual procedure is:

```bash
read -r -s -p 'Bootstrap key: ' LUCIEN_BOOTSTRAP_KEY
printf '\n'
export LUCIEN_BOOTSTRAP_KEY
lucien create user administrator
unset LUCIEN_BOOTSTRAP_KEY
```

`lucien create user` creates exclusively the first administrator. It is not a
general registration command. Later users are created by the administrator through
the Hub IAM API.

On success, the permanent credential is displayed exactly once and saved for the
same operating system user. Store it in a vault before clearing the terminal, and
validate it immediately:

```bash
lucien auth status
```

Do not run the bootstrap with `sudo` if the regular account will use the CLI; the
profile and the vault belong to the account that runs the command.

After success, close the window on the server right away:

```bash
sed -i 's/^USER_CREATION_ENABLED=.*/USER_CREATION_ENABLED=false/' .env
chmod 0600 .env
docker compose --env-file .env -f docker-compose.local.yml \
  up -d --force-recreate hub
```

Validate `/health` again. The database also prevents a second bootstrap, but the
disabled flag reduces the exposed surface and keeps the operational intent clear.

## 8. CLI command reference

### `API_HOST` format

The CLI accepts only the Hub origin — scheme, host, and port:

```
API_HOST=https://lucien-api.interno:8443
```

An embedded credential, a path, a query, or a fragment is refused at startup. This
is not fussiness: the value prefixes every call and composes the account name in
the keyring. A `user:password@` would write a credential into the entry name and
leak it anywhere the address is logged; a path would silently shift every
endpoint, and the error would show up as a `404` from the Hub instead of as
invalid configuration.

A trailing slash is accepted: `https://hub:8443` and `https://hub:8443/` describe
the same origin.

### `lucien --version`

Shows the version written into the binary at packaging time:

```
lucien version 1.1.5
```

A binary compiled locally, without going through `scripts/build-cli.sh`, shows
`dev` — which already distinguishes a development build from a published package.
It is the first thing to check when one machine's behavior diverges from another's.

### `lucien help`

Shows the available commands. Use `lucien <command> --help` to look up specific
arguments and flags.

### `lucien create user <name>`

Creates the first administrator through the bootstrap and activates their local
profile. It requires the `USER_CREATION_ENABLED=true` window on the Hub and
`LUCIEN_BOOTSTRAP_KEY` for that run only. Do not use it for regular users.

### `lucien login`

Asks for a credential without echo. A provisional credential is exchanged exactly
once for a permanent one; a permanent one is validated at `/me`. The result is
kept in the operating system user's keyring:

```bash
lucien login
```

If the exchange response is lost, the CLI retries once with the same idempotency
key; the Hub returns the same permanent credential, without creating another one.

The CLI never sets its own role or domain; that data always comes from the Hub.

`login` installs a new credential; it does not show the current session. To
validate the credential already saved, use `lucien auth status`.

### Registering and administering the remaining users

!!! warning "The `admin user` flags were renamed"
    `--role` now means **area** (the same thing as the `-r` of `lucien start`),
    and it accepts a list. The permission level, which used to use `--role`, is
    now `--level`. `--domain` no longer exists: use `-r`.

    An old script with `--role senior` does not do anything silently wrong — the
    Hub refuses with `área 'senior' não existe`, because `senior` is not in
    `RUNBOOK_DOMAIN_FUNCTIONS`. That message comes from the Hub, which has not
    been internationalized, so it appears in Portuguese regardless of this
    documentation's language.

    ```bash
    lucien admin user create joao --level senior -r servidores,acessos
    ```

Once authenticated as an admin:

```bash
lucien admin user create operador.rede \
  --role junior \
  --domain redes
```

The provisional credential appears exactly once, expires in four hours, and allows
a single exchange. Transfer it through a corporate vault or an approved secure
channel; do not send it over chat, email, or a recorded session. On the user's
machine:

```bash
lucien login
# Paste the token into the prompt, without echo.
lucien auth status
```

Additional administrative operations accept a UUID or a username:

```bash
lucien admin user update operador.rede --role pleno --domain redes
lucien admin user issue-provisional-token operador.rede
lucien admin user revoke operador.rede --yes
```

The new provisional credential immediately invalidates the previous ones.
Revocation requires `--yes` to avoid accidental execution.

If user creation ends with an uncertain network result, do not create another
username. Run `issue-provisional-token` for the same username: if the creation was
confirmed on the Hub, a new provisional credential safely replaces the one that
was not delivered; if it was not, the Hub answers `404`.

### Recovering an administrator with no valid token

Do not reopen the bootstrap and do not erase the database. On the Hub host, after
installing this updated version:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec hub python -m app.recover_admin Admin
```

On the administrator's client:

```bash
lucien login
# Paste the token that was just displayed.
lucien auth status
```

Recovery is a privileged local operation of the Hub. It issues a provisional
credential for four hours, exposes no recovery route on the network, and records
the event without including the credential.

### Reproducing the published tree from the database

Every publication is mirrored into PostgreSQL: the complete Markdown, frontmatter
included, and the image bytes (`published_documents` and `published_assets`). The
Git destination is still where the artifact lives, but it is no longer the only
place the content exists.

This exists so a future decision has a path: hosting the collection on a local
wiki, leaving Gitea or GitHub, or simply checking what was published without
cloning anything. The export writes a tar to `stdout` — the Hub container runs
`read_only`, so the host is what writes to disk, just like `backup-db.sh`:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec -T hub python -m app.export_wiki > wiki.tar
mkdir -p wiki && tar -xf wiki.tar -C wiki
```

`-T` is required: without it Docker allocates a TTY and corrupts the tar. The
report of how many runbooks came out goes to `stderr`, so it never mixes into the
archive.

The extracted content is the same tree the `local` provider produces — the one
`wiki-builder` consumes — so MkDocs builds straight on top of it. The stored path
is the one relative to the documents root, without the Git provider's prefix, so
the same export serves any destination. A revision that inherited an image without
changing it does not duplicate it: the bytes stay under the ancestor's job, and
exporting the whole tree writes them from there, exactly as Git does today.

Deleting a user's row in the database no longer deletes their runbooks: the
foreign key is `RESTRICT`, and PostgreSQL refuses. A published runbook is the
team's knowledge, not the property of whoever wrote it. The way out is still
deactivating the account, which preserves everything.

### Using on a jump server {#usar-em-jump-server}

Every operator needs an individual Unix account provided by SSSD. After installing
the CLI and the CA, issue a dedicated service credential on the Hub host:

```bash
docker compose --env-file .env \
  -f docker-compose.local.yml \
  exec -T hub python -m app.issue_jump_enrollment_key
```

The `luc_jump_...` value is displayed once. Transfer it through a secure
administrative channel and do not paste it into tickets, shell history, or user
files. On the jump server, with the complete repository and the CLI already
installed, run:

```bash
chmod +x deploy/install-jump-server.sh
sudo ./deploy/install-jump-server.sh
```

Provide the Hub HTTPS URL, the public CA, the local administrative account, the
matching username on the Hub (`Admin`), and the M2M credential. The installer
writes the credential to `/etc/lucien/secrets/jump_enrollment_key` (`root:root`,
`0600`), installs the sudoers-restricted helper, the banner, and the hook in
`/etc/profile.d`, and validates TLS and SSH.

On the first LDAP login, the helper queries the Hub by the same POSIX ID. If the
identity does not exist, it asks exactly once:

1. Access (`acessos`);
2. Servers (`servidores`);
3. Network (`redes`);
4. Support (`suporte`).

The numbered labels are what the operator reads; the values in parentheses are
what travels to the Hub, and they are the same in every installation.

A new user's initial role is always `pleno`, below `senior`. Existing `junior`,
`pleno`, or `senior` identities keep their role and domain entirely; `admin`
accounts use the separate administrative flow. The provisional token is exchanged
by the CLI over `stdin`, and the permanent one stays in the keyring or in the
account's own `0600` fallback. On later logins, `/me` is validated silently. The
local administrative account uses the `Admin` identity already configured; if the
vault is empty, its token is requested without echo.

Jump mode does not change ordinary CLI installations. Outside that host, do not
set `LUCIEN_JUMP_MODE` or `LUCIEN_EXPECTED_USERNAME`: the operator keeps running
`lucien login` with their personal token and uses every command without LDAP or an
M2M credential.

On the Hub firewall, restrict access to `/auth/jump/enroll` to the jump server
origin whenever the topology allows it. To rotate the M2M credential, run the
module on the Hub again and then the installer on the jump server; the previous
credential stops working immediately.

Do not use shared Unix accounts. The keyring, the profile, the drafts, and the
fallback files belong to the local account; sharing that account eliminates the
isolation between operators.

### `lucien start <project_name> [-d "description"]`

Opens a PTY and starts recording the session locally. The name identifies the task
or project; it does not choose GitHub, Gitea, or the storage provider. `-d` or
`--describe` accepts up to 280 characters and is optional, but recommended to
improve the SLM's extraction.

```bash
lucien start manutencao-redis \
  -d "Diagnose Redis replication and latency"
```

The PTY is created with your terminal's size and follows resizing through
`SIGWINCH`. With no originating terminal — running through a pipe or a scheduler —
it assumes 80x24. That matters for SSH sessions opened inside the recording: the
SSH client propagates the local dimensions to the remote equipment, and an OLT,
CMTS, or router that receives zero lines draws nothing, leaving the session
apparently frozen.

SSH sessions to network equipment are recorded like any other: the commands typed
into the equipment's CLI and their outputs enter the log the same way, with no
extra configuration.

### What the `-d` description becomes in the document

The text from `lucien start -d` appears as a subheading of the `## Objetivo`
section. With `SLM_LANGUAGE_RUNBOOK=en`:

```markdown
## Objective

### Commands to check a down route on ZTE OLTs

> **MANDATORY REVIEW — OPERATOR DESCRIPTION:** text provided at capture time;
> complete the objective before publication.
```

Replace the quoted text with the objective itself; the subheading already
identifies the subject and is what appears in the wiki index.

### Publishing to another domain function

`lucien start <name> -r <function>` chooses the publication destination directory
at capture time:

```bash
lucien start exemplo -r acessos -d "my publication in another role"
```

The artifact goes to `<year>/acessos/` instead of the author's domain. Without
`-r`, the destination stays your own domain — the usual behavior.

Two rules apply here, and both belong to the Hub, not the CLI:

The function must exist in `RUNBOOK_DOMAIN_FUNCTIONS`. If it does not, the upload
is refused and the message lists the available ones. `lucien start` only validates
the grammar (lowercase, 3 to 64 characters) because it records offline and does
not know the Hub's configuration.

The domain is a scope of authority, not a preference: a `senior` from `servidores`
who asks for `-r acessos` gets `403`. Only `admin` publishes outside their own
domain. The high-criticality restriction for `junior` still applies on top of
that, unchanged.

A note on vocabulary, because `-r` uses the word "role" in a specific sense. Here
**role is the area** — `acessos`, `servidores`, `roteamento` — and it is what
becomes a directory. It is what the code calls `domain_function`.

Do not confuse it with the **permission level** (`junior`, `pleno`, `senior`,
`admin`), which the code calls `role_level`. That one stays hard-coded, because
each level carries its own RBAC rule.

And neither of the two is the person's **job title**. Lucien does not model job
titles: whether a coordinator is `senior` or `junior` here is your organization's
decision, and it may bear no relation to their title.

### Working in more than one area

If you serve more than one area, the admin grants both at once:

```bash
lucien admin user update U000004 -r servidores,acessos
```

The first one is the primary — the destination without `-r`. Check what you hold:

```bash
lucien auth status
```

```
Authenticated as U000004 (11111111-1111-4111-8111-111111111111); level=senior areas=servidores, acessos.
```

### `lucien stop`

Ends the capture and preserves the log locally. It sends nothing to the Hub. That
separation lets you stop the recording even when the network or the API is
unavailable. Run `lucien stop` directly inside the recorded shell; the command
ends the PTY and returns the original terminal. It can also be run from a second
terminal under the same account.

On exit, the terminal receives the reminder of the next step:

```
Session olt-rota-down-20260819-233443-2f7add630842 stopped and preserved locally.
Next: lucien upload
After acceptance, upload will return the Job_ID and status command.
```

The `lucien start` process is what prints it, after restoring the terminal. That
also applies when you simply leave the recorded shell with `exit`, without using
`lucien stop`: the session is preserved just the same and the reminder appears,
because nothing has been sent to the Hub yet and the log is still waiting for
`upload`.

### `lucien upload [-s|--skip-enrichment]`

Sanitizes ANSI escapes and sends the last finished session to the Hub. Acceptance
returns quickly with a `Job_ID` and `PROCESSING`; the SLM runs in the
`upload-worker`. The CLI only removes the local files after the `202 Accepted`. On
a failure before acceptance, it preserves the session and reconciles by name
before creating another job.

`--skip-enrichment` skips the second SLM call for this job. Command extraction
still happens; the draft comes out with the basic structure and without a
suggested goal, impacts, or rollback. The opt-out belongs to the operator and
prevails even with `SLM_ENRICHMENT_ENABLED=true` on the Hub. Use it on hosts where
inference is too slow to fit within `SLM_TIMEOUT_SECONDS`.

### `lucien job status <id_or_name_or_index>`

Shows `PROCESSING`, `PENDING`, `FAILED`, or `PUBLISHED`. On `FAILED`, it shows a
safe diagnostic code, never log content.

### `lucien job retry <id_or_name_or_index> [-s|--skip-enrichment]`

Requeues one of your own `FAILED` jobs. The sanitized payload stays encrypted in
PostgreSQL until processing completes, or the owner deletes or requeues it.

Without the flag, the retry preserves the choice made in the original `upload`.
With `--skip-enrichment`, the reprocessing starts skipping enrichment — the
indicated path when the job failed with `UPSTREAM_ERROR` due to an SLM timeout.

### `lucien reviews`

Lists the authenticated user's queue with ID, name, status, and date. It includes
`PROCESSING`, `PENDING`, and `FAILED` jobs; completed publications do not appear.
For continuous monitoring on Linux, use `watch -n 5 lucien reviews`.

Every job command (`job`, `status`, `retry`, `sent`, and `del`) accepts the 1-based
position shown by `lucien reviews`, besides the UUID or the name. Since the queue
can change, check `reviews` immediately before using an index for publishing or
deleting.

### `lucien job <id_or_name_or_index>`

Downloads the detected commands, opens an interactive selection, and starts the
editor configured in `EDITOR`. On closing the editor, it saves a local draft with
restricted permission. The command does not publish the document yet. A positive
number references the 1-based position shown by `lucien reviews`; IDs and names
are still accepted.

### `lucien job cat <job_id>`

Prints the saved draft, without opening the editor. Read-only: it changes
nothing.

It exists to diagnose a refusal. When the Hub blocks a publication, the message
names the rule; this command hands you the text so you can find the line. Output
goes to `stdout`, so it takes a pipe:

```bash
lucien job cat f51201f2-388a-4ce5-99ea-5d59f9424ca9 | grep -n -i 'senha\|password'
```

The content comes from the **local draft**, and the command **never contacts the
Hub** — a refused draft never reached it, and a diagnostic that depended on the
Hub would fail exactly when there is something to diagnose.

That is why it requires the exact ID and accepts neither an index nor a name:
resolving those would need the Hub's list. `lucien runbook revise` requires the
UUID for the same kind of reason. Take the ID from `lucien reviews` or from the
output of `lucien job`.

The command **refuses to run inside a recorded session**. It dumps the draft to
the terminal, and there that would enter the capture itself — including the
secret that caused the refusal. Run it from another terminal.

It uses no pager. To paginate, pipe it: `lucien job cat <id> | less`.

### `lucien job sent <id_or_name_or_index>`

Sends the draft to the Hub with an idempotency key derived from the user, the job,
and the content. The Hub runs secret scanning, DLP, Markdown validation, RBAC, and
frontmatter injection. The local draft is only removed after the publication is
confirmed.

### `lucien job del <id_or_name_or_index> [-y]`

Deletes one of your own `PENDING` or `FAILED` jobs. To cancel and purge a job
stuck in `PROCESSING`, use `lucien job del <id_or_name_or_index> --force`; removal
from the queue is transactional. `--yes` skips only the interactive confirmation.
No combination of flags deletes a `PUBLISHED` job.

### `lucien runbook cat <published_runbook_uuid>`

Prints a published runbook without opening the editor. Pure reading: it changes
nothing.

It is to `revise` what `job cat` is to `job` — read what is there without the
risk of editing it. Consulting a procedure during maintenance should not put the
operator in front of an editor with an immutable publication open.

Unlike `job cat`, this one **does query the Hub**: a published runbook exists
only there. There is no refused-draft dilemma — the content already passed
secret scanning and the DLP before being published.

It requires the exact UUID, for the same reason `revise` does: both operate on
the same publication, and accepting different identifier forms would let you
read one runbook and revise another.

Output goes to `stdout`, so it accepts a pipe:

```bash
lucien runbook cat 3e381ebe-0284-4d3b-b304-a13655e3dd4c | less
```

The command **refuses to run inside a recorded session**. Here the reason is not
a secret — the content already passed the policy — but that the whole runbook
would enter the log as the last command's output, and the next one would be born
with another embedded inside it. Run it from another terminal.

In the desktop app (`lucien-desktop`) the equivalent is clicking a runbook in
the "Published" tab: it opens the published content in read-only mode, with a
"Revise" button that switches to editing when needed — the recorded-session
restriction does not apply there, since the app does not run inside a PTY.

### `lucien runbook revise <published_runbook_uuid>`

Corrects an already published runbook. It downloads the body from the Hub, opens
`EDITOR`, and returns the result; the Hub creates a **new immutable version** with
another UUID, preserves the previous one, and records the lineage
(`runbook_raiz`, `revisao`, `substitui`). It works on all three providers —
`local`, `github`, and `gitea` — under the same rules.

It requires the exact publication UUID. Unlike the job commands, `revise` does
**not** accept an index from `lucien reviews`, nor a name: a one-digit error in
the index would publish the correction over the wrong runbook, and the queue
changes between one command and the next. Take the UUID from the portal, from the
published artifact's URL, or from the output of the `lucien job sent` that created
it.

```sh
lucien runbook revise 3e381ebe-0284-4d3b-b304-a13655e3dd4c
```

Only `admin` and `senior` review; `senior` is restricted to the immutable domain
of the root publication. Outside it the answer is `404`, not `403`, so as not to
confirm that the runbook exists. With `RBAC_ENTRY_ROLES_ENABLED=true`, junior and
pleno also review within their own domain.

The downloaded body comes **without** frontmatter: it is generated by the Hub and
refused if it comes from the client. `ultimo_revisor` and `data_revisao` are filled
in by the Hub itself, with whoever published this version and when — not by pasting
frontmatter into the editor. The root's provenance (`autor`, `nivel_autor`, `funcao`,
`data_criacao`) is copied from the first version and never shifts to the reviser: the
runbook is one thing, and whoever wrote it remains whoever wrote it.
Closing the editor without changing anything cancels the operation without
consuming a new UUID. A `412` means another revision was published while yours was
open: run the command again to start from the current version.

## 9. First complete flow

After authenticating:

```bash
lucien start manutencao-redis \
  -d "Validate Redis replication after maintenance"
```

Run the maintenance commands in the shell Lucien opens. In that same recorded
shell, end and preserve the session:

```bash
lucien stop
```

Back in the original terminal, continue:

```bash
lucien upload
lucien job status <JOB_ID>
lucien reviews
lucien job <JOB_ID>
lucien job sent <JOB_ID>
```

As an alternative, `exit` ends the recorded shell and already leaves the session as
`STOPPED`; in that case, go straight to `lucien upload`. There is no need to run
`exit` before `lucien stop`. Never record commands that print tokens, passwords,
or keys; DLP and secret scanning are containment barriers, not a vault.

## 10. Next step for Gitea Actions

Only after creating the administrator and closing the bootstrap:

1. confirm that `.gitea/workflows/deploy.yml` watches the same branch as
   `GIT_BRANCH`;
2. enable Actions in Gitea and in the repository;
3. register the runner on the dedicated host:

   ```bash
   ./deploy/install-hub.sh --configure-gitea-runner
   ```

4. prepare the SSH key and access to Nginx on the administrative host:

   ```bash
   ./deploy/install-hub.sh --prepare-nginx-deploy
   ```

Those modes must not be run inside the Hub's operational copy: the installer
refuses the runner configuration when it detects a Hub `.env` or Compose file in
the same directory. See [Wiki publication](publicacao.md) for the workflow
secrets, the runner restrictions, and the Nginx configuration.

## 11. Final checklist

- [ ] `/health` answers with the correct CA and without `-k`.
- [ ] TCP/8443 accepts only authorized networks.
- [ ] PostgreSQL, the SLM, and the scanner are not published.
- [ ] The Gitea URL does not contain `//api/v1`.
- [ ] `GIT_BRANCH` and the workflow watch the same branch.
- [ ] The Git token has minimal scope and belongs to a service identity.
- [ ] The first administrator was created and `USER_CREATION_ENABLED=false` was
      applied.
- [ ] Only `ca.crt` was distributed to the clients.
- [ ] `ca.key` and old keys were removed from the host after a validated backup.
- [ ] The CLI runs natively and can repeat `upload` after a network failure.
