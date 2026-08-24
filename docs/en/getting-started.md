# Quick start

[Português (Brasil)](../tutorial.md) · **English**

## Requirements

- Linux host with Docker Engine and Docker Compose v2;
- Git for cloning the repository;
- a supported native Linux or macOS environment for the CLI;
- DNS and firewall access between the CLI and the Hub on TCP 8443.

Do not expose PostgreSQL, Ollama, or the secret scanner directly. Keep `.env`,
tokens, private certificates, local profiles, recordings, and drafts outside
version control.

## Install the Hub

The guided installer creates a restricted `.env`, generates TLS certificates
when they are missing, and lets you select a publication backend.

```bash
chmod +x deploy/install-hub.sh
./deploy/install-hub.sh
```

The available presets are:

1. local immutable storage with the authenticated portal;
2. GitHub Contents API with GitHub Pages;
3. Gitea Contents API with a compact, fixed wiki builder;
4. Gitea Contents API with a dedicated Actions runner.

For an existing installation, refresh the operational Compose file without
overwriting `.env`, certificates, or secrets:

```bash
./deploy/install-hub.sh --refresh-compose
```

## Install the CLI

Install the native CLI on the operator host. Only the public CA certificate is
copied to the client.

```bash
chmod +x deploy/install-cli.sh
./deploy/install-cli.sh
```

Then authenticate and record a session:

```bash
lucien login
lucien start inspect-dns -d "Validate DNS resolution"
# Run the commands that belong to the procedure.
lucien stop
lucien upload
```

The upload returns a Job ID. Wait for `PENDING`, select the useful commands,
review the generated Markdown, and publish it:

```bash
lucien job status <job-id>
lucien job <job-id>
lucien job sent <job-id>
```

See the [complete Portuguese tutorial](../tutorial.md) for identity bootstrap,
LDAP, TLS, RBAC, retries, revisions, and production operations.
