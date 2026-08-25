# Isolated deployment: CLI, API, and TLS

This guide separates the artifacts needed to run only the Lucien CLI or only the
Runbook API Hub. The two components talk exclusively over `API_HOST` and TLS;
there is no address baked into the binary.

For a complete guided procedure, including installer answers, bootstrap, and the
CLI command reference, see the [Installation manual](manual-instalacao.md).

!!! warning "Certificates are not born at `up`"
    `docker compose up` does not generate certificates. Generate them explicitly,
    once, before starting the Hub. The project ships the `certgen` service for
    that; the equivalent OpenSSL commands are in this guide for operation without
    Docker.

## Running only the native CLI

This is the official path for PTY capture: run the binary in the operator's Linux
or macOS terminal. A CLI inside a container would record the container's own
terminal, not the user's real session. Windows has no PTY support in this
project.

| Artifact | Required | Purpose |
| --- | --- | --- |
| `lucien` binary | yes | executable used in the operator's terminal |
| `deploy/install-cli.sh` | recommended on Linux | detects the architecture, installs binary/CA and completion, and persists the public environment |
| `cli/` directory | no | source code, used only in the central build environment |
| system environment variables | yes | define `API_HOST`, `TLS_CA_FILE`, and `EDITOR` |
| `certs/ca.crt` | yes | validates the Hub TLS certificate |
| `certs/ca.key`, `server.key`, `server.crt` | no | must never be copied to the client |

`docker-compose.yml`, `docs/`, `backend/`, `secret-scanner/`, `certgen/`,
PostgreSQL, and the SLM are not needed on that node. The `.env.client.example`
file is a value reference: the Go binary does not read `.env` files on its own;
inject the variables through the shell, systemd, MDM, or a corporate vault.

The [usage tutorial](tutorial.md) shows how to obtain the prebuilt Linux/macOS
package, verify its checksum, and install it into `~/.local/bin`. After
installation, configure the operator's terminal.

On Linux, the preferred path is the dedicated client installer:

```sh
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

It asks for the `.tar.gz` file, the matching checksum, the Hub HTTPS URL, and the
path to a copy of `ca.crt`. The CA is not created on the client: it must be the
public CA that signed the Hub certificate. The script installs completion for the
Bash, Zsh, or Fish login shell and prints, at the end, the paths of the binary,
the CA, the environment file, the completion, and the shell profile.

```sh
export API_HOST="https://runbook.example.internal:8443"
export TLS_CA_FILE="/etc/lucien/ca.crt"
export EDITOR="vi"

lucien login
lucien start redis-cache -d "Validate replication"
```

The name in `API_HOST` must appear in the server certificate SAN. The CLI refuses
HTTP and validates against the CA named by `TLS_CA_FILE`.

Compose does not run the CLI. Docker is reserved for the Hub and its supporting
services.

For an automated jump server, additionally transfer
`deploy/install-jump-server.sh` and the whole `deploy/jump/` directory. Those
files are not needed on a personal workstation or WSL; in those environments the
flow stays `lucien login` with the individual credential.

## Running Hub/API and SLM on the same machine

This is the recommended scenario when the Gitea installation already exists on
another machine. Use the `consolidated` profile: it starts PostgreSQL, the Hub,
the Secret Scanner, Ollama/SLM, and the model initializer on the same host. Gitea
stays external and is reached exclusively through the REST API configured in
`GIT_API_BASE`.

| Artifact | Required | Purpose |
| --- | --- | --- |
| `docker-compose.yml` | yes | orchestrates Hub, PostgreSQL, scanner, and SLM |
| `backend/` directory | yes | Hub API image |
| `secret-scanner/` directory | yes | Gitleaks, mandatory in *enforce* mode |
| `.env` derived from `.env.example` | yes | database, authentication, local SLM, Gitea, and SANs |
| `certs/server.key`, `certs/server.crt`, `certs/ca.crt` | yes | Hub TLS and local healthcheck |
| `certgen/` directory | only when issuing/rotating | generates the CA and the Hub certificate |
| `deploy/install-hub.sh` | guided install and auxiliary modes | creates `.env`/Compose, or configures runner/SSH on separate hosts |
| `deploy/systemd/act-runner.service` | only for Gitea Actions | hardened unit installed on the dedicated runner host |
| `runbook-viewer/` and `logo-lucien.png` | only for `local-viewer` | authenticated portal over HTTPS/9091; read-only volume and revisions through the Hub |
| `wiki-builder/` and `deploy/nginx/wiki-compact.conf` | only for `gitea-compact` | fixed builder and static server with no Docker socket |
| `cli/`, `docs/`, `site/`, local MkDocs, and workflows | no | take no part in running the Hub or the two fixed services |

### Complete structure to copy to the Hub server

To build the images on the server itself and generate the certificates there,
copy this structure, preserving names and hierarchy:

```text
lucien-hub/
├── docker-compose.yml              # or docker-compose.local.yml, already generated
├── docker-compose.build.yml        # local build, separate from runtime
├── .dockerignore                   # protects the build context
├── .env                            # local configuration; never commit it
├── backend/                        # build context of the FastAPI API
├── secret-scanner/                 # build context of the Gitleaks scanner
├── runbook-viewer/                 # only in the local-viewer preset
├── logo-lucien.png                 # only in the local-viewer preset
├── wiki-builder/                   # only in the gitea-compact preset
├── certgen/                        # needed to issue/rotate TLS
├── certs/
│   ├── ca.crt                      # public CA used in the healthcheck
│   ├── server.crt                  # Hub certificate
│   └── server.key                  # Hub private key
├── secrets/                        # 0700 directory; 0444 files for Compose
└── deploy/
    ├── install-hub.sh              # optional; guided install only
    └── nginx/
        └── wiki-compact.conf       # only in the gitea-compact preset
```

Rules for safely reducing the set:

- if the certificates were already generated in a controlled environment,
  `certgen/` is not needed at runtime; copy only the three TLS files shown above;
- if `.env` and Compose are already prepared, `deploy/install-hub.sh` is not
  needed on the server;
- in the `consolidated` profile there is no other local directory for the SLM:
  Docker pulls the Ollama image and keeps the data in the named volume
  `ollama-data`;
- with `STORAGE_PROVIDER=local`, the runbooks live in the named volume
  `playbooks-data`; the portal mounts that volume as `:ro`, and the `site/`
  directory is not an API destination;
- with GitHub or Gitea, the Hub publishes through the provider's REST API. There
  is no need to copy the wiki repository or the `site/` directory to the Hub
  server. In compact mode, the builder keeps its clone and cache in its own
  Docker volumes.

Do not copy to the API-only host: `cli/`, `docs/`, `site/`, `scripts/`,
`.github/`, `.gitea/`, `mkdocs.yml`, or `requirements-docs.txt`.
`runbook-viewer/`, `logo-lucien.png`, `wiki-builder/`, and
`deploy/nginx/wiki-compact.conf` can also be omitted when their respective
presets are not used. The `certs/ca.key` key, created during issuance, is not
needed to run the Hub and should be moved to an offline vault afterwards.

If you did not use the guided installer, create the file before editing it:

```powershell
Copy-Item .env.example .env
```

Configure the non-sensitive values in `.env`. Credentials must be files without a
trailing newline in `secrets/`, with a `0700` directory and `0444` files. The
manual flow is more error-prone; prefer `deploy/install-hub.sh`.

```dotenv
COMPOSE_PROFILES=consolidated
HUB_BIND_ADDRESS=0.0.0.0
SLM_BASE_URL=http://slm:11434
SLM_MODEL=qwen2.5-coder:3b
SLM_LANGUAGE_RUNBOOK=pt-br

STORAGE_PROVIDER=gitea
GIT_API_BASE=https://gitea.example.internal/api/v1
GIT_OWNER=infrastructure
GIT_REPO=runbooks
GIT_BRANCH=main
GIT_DOCS_PREFIX=docs/runbooks
```

`SLM_LANGUAGE_RUNBOOK` chooses the language of the *generated runbooks*, and it
is independent of this documentation's language. It accepts `pt-br` or `en`.

Then generate the TLS material and bring the environment up:

```powershell
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  --profile tools build certgen
docker compose --profile tools run --rm certgen
docker compose -f docker-compose.yml -f docker-compose.build.yml \
  --profile consolidated build
docker compose --profile consolidated up -d
docker compose --profile consolidated logs -f slm-init
```

Do not publish PostgreSQL, the SLM, or the Secret Scanner. Expose only the Hub's
TCP 8443 to authorized origins. Compose mounts `secrets/` at `/run/secrets`,
which keeps the values out of `docker inspect`, but does not protect against root
or against access to the Docker socket. Use Vault/KMS when the threat model calls
for that separation.

The `server` profile stays available only for a deployment where the SLM runs on
another private host. In that case, use `.env.server.example` and configure
`SLM_BASE_URL` with the remote endpoint. Do not use that profile when the SLM
should sit next to the Hub.

## Guided Hub installer

On a Linux host with Docker Compose v2 and OpenSSL, run the installer from the
root of the isolated Hub package. That package still needs `docker-compose.yml`
at its root, since it is the model used to generate the local Compose:

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

If you already copied the directories and got a missing-artifact error, copy the
three files from the same release to the package root:

```sh
cp /path/to/projeto_lucien/docker-compose.yml ./docker-compose.yml
cp /path/to/projeto_lucien/docker-compose.build.yml ./docker-compose.build.yml
cp /path/to/projeto_lucien/.dockerignore ./.dockerignore
```

Then run:

```sh
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

It asks for the Hub FQDN, whether to expose TCP/8443, local or remote SLM, one of
the four presets (`local-viewer`, `github`, `gitea-compact`, `gitea-runner`), and
the controlled opening of the bootstrap. At the end it creates:

| File | Purpose |
| --- | --- |
| `.env` | non-sensitive configuration and the image tag, with permission `0600` |
| `docker-compose.local.yml` | editable copy of the base Compose structure |
| `secrets/` | `0444` files, protected on the host by the `0700` directory |

The script issues the certificates automatically when `ca.crt`, `server.crt`, and
`server.key` are missing; if the set is complete, it reuses it without rotating.
A partial set stops the installation. The script asks for confirmation before
starting the services and refuses to overwrite `.env` or
`docker-compose.local.yml`. It does not install Docker, does not touch the CLI,
and does not print the generated secrets to the terminal.

Use the local Compose file for later operation:

```sh
docker compose --env-file .env -f docker-compose.local.yml \
  -f docker-compose.build.yml build
docker compose --env-file .env -f docker-compose.local.yml up -d
docker compose --env-file .env -f docker-compose.local.yml logs -f hub
```

For GitHub or Gitea, provide in the dialog a publication token with the minimum
scope needed to change only the runbooks repository. The token lands in
`secrets/git_token`, mounted only in the Hub.

## Generating certificates with the project tool

This is the preferred path: the script uses RSA 4096 bits for the CA, RSA 3072
bits for the server, explicit SANs, and restrictive permissions.

1. Before generating, set in `.env` the names the clients actually use:

   ```dotenv
   CERT_DNS=runbook.example.internal,hub,localhost
   CERT_IP=127.0.0.1
   CERTS_DIR=./certs
   ```

2. Generate once:

   ```powershell
   docker compose --profile tools run --rm certgen
   ```

3. Distribute only `certs/ca.crt` to the clients. Keep `certs/ca.key` off the
   application host whenever possible; `certs/server.key` stays only on the Hub.

The generator refuses to overwrite existing keys. To rotate, generate a new set in
a secure directory, swap the CA on the clients in a coordinated way, and only then
change the Hub certificate. Do not delete certificates in use as a shortcut.

If the Hub shows up as `unhealthy` with `CA cert does not include key usage
extension`, the CA was issued by an old version of the generator. Do a coordinated
rotation: keep the current files as a backup, generate a new set with the updated
`certgen`, distribute only the new `ca.crt` to the clients, and recreate the Hub.
The CA must carry `keyUsage=critical,keyCertSign,cRLSign`; `openssl verify` alone
does not detect that omission in every OpenSSL/Python version.

## Generating certificates manually with OpenSSL

Use this procedure when you cannot use the `certgen` container. Replace
`runbook.example.internal` with the FQDN present in `API_HOST`; include every name
or IP the clients actually use.

```powershell
New-Item -ItemType Directory -Force certs | Out-Null

# Private CA: keep it off the Hub host after issuing the certificate.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out certs/ca.key
openssl req -x509 -new -sha256 -days 3650 `
  -key certs/ca.key `
  -subj "/C=BR/O=Lucien/CN=Lucien Internal CA" `
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" `
  -addext "keyUsage=critical,keyCertSign,cRLSign" `
  -addext "subjectKeyIdentifier=hash" `
  -out certs/ca.crt

# Hub key and CSR.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out certs/server.key
openssl req -new -sha256 `
  -key certs/server.key `
  -subj "/C=BR/O=Lucien/CN=runbook-hub" `
  -out certs/server.csr

# Server certificate extensions and SANs.
@'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:runbook.example.internal,DNS:hub,DNS:localhost,IP:127.0.0.1
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

On a Linux host, adjust access for the container's unprivileged user before
starting the Hub:

```sh
chmod 0600 certs/ca.key certs/server.key
chmod 0644 certs/ca.crt certs/server.crt
chown 10001:10001 certs/server.key certs/server.crt
```

On Docker Desktop, prefer `certgen`: it already applies the expected permissions
and avoids ACL differences between Windows and Linux. Never send `ca.key` or
`server.key` by email, repository, chat, or to the CLI node.
