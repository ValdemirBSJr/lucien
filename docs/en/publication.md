# Publication backends

[Português (Brasil)](../publicacao.md) · **English**

The Hub publishes through one of three storage providers. The CLI never writes
to Git or chooses the destination path. New artifacts use
`docs/runbooks/<year>/<area>` and published files remain immutable.

## Local portal

Select the `local-viewer` preset in `deploy/install-hub.sh`. The Hub writes to a
local immutable volume and the authenticated portal reads that volume as
read-only on HTTPS port 9091. No Git repository or CI pipeline is required.

Use this mode for an isolated proof of concept or for environments where Git is
not an acceptable publication dependency.

## GitHub Pages

Select the GitHub preset and provide a repository-scoped fine-grained token.
The Hub writes Markdown through the GitHub Contents API. The trusted workflow in
`.github/workflows/deploy.yml` builds MkDocs and publishes the site with GitHub
Pages.

In the repository, select **Settings → Pages → Source → GitHub Actions**. Treat
the resulting site as public unless your organization and plan explicitly
support private GitHub Pages. Protect the default branch and review changes to
the workflow and MkDocs hook.

## Gitea compact

Select `gitea-compact` when the Gitea service is external but the wiki can be
built on the Hub host. The fixed `wiki-builder` image reads only Markdown from
the repository, never mounts the Docker socket, and does not execute workflows,
hooks, plugins, or repository-provided MkDocs configuration.

This is the recommended Gitea mode when simplicity and isolation are more
important than a general-purpose CI pipeline.

## Gitea with a dedicated runner

Select the advanced runner preset only when Gitea Actions is required. Install
the runner on a dedicated VM, not on the Hub, database, SLM, or Gitea host. A
runner with access to a Docker socket is highly privileged. The deployment
workflow uses SSH/rsync, verifies the pinned host key, and promotes releases
atomically.

The project includes the workflow in `.gitea/workflows/deploy.yml`, the hardened
systemd unit under `deploy/systemd/`, and the guided commands:

```bash
./deploy/install-hub.sh --configure-gitea-runner
./deploy/install-hub.sh --prepare-nginx-deploy
```

For the complete environment variables, secrets, pre-receive Gitleaks hook,
rollback procedure, and threat boundaries, use the
[Portuguese publication guide](../publicacao.md).
