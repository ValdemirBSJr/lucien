# Lucien Runbooks

This wiki gathers operational procedures captured by Lucien, filtered by an SLM
and necessarily reviewed by a person before publication.

## Start here

- [Installation manual](manual-instalacao.md): install the Hub and the CLI, run
  the bootstrap, and look up every client command.
- [Usage tutorial](tutorial.md): run the first end-to-end flow.
- [Isolated deployment and TLS](implantacao-isolada.md): run the CLI alone or the
  API alone, and generate the certificates you need.
- [Technical documentation](documentacao-tecnica.md): architecture, API,
  configuration, and limitations.
- [Operation and security](operacao.md): the mandatory standard for runbooks.
- [IAM, RBAC, and metadata](iam-rbac.md): identity and authorization.
- [Wiki publication](publicacao.md): local portal, GitHub Pages, and two Gitea
  modes.

## Publication flow

1. The operator records a session with `lucien start <name> -d "short
   description"`. The description is optional, but recommended: it improves the
   context the SLM works from.
2. `lucien stop` ends the PTY and preserves the session locally.
3. `lucien upload` sanitizes, encrypts, and enqueues a `PROCESSING` job.
4. The worker extracts the commands and moves the job to `PENDING`.
5. `lucien job <id_or_name_or_index>` lets you select commands and write the
   procedure; the index matches the position shown by `lucien reviews`.
6. `lucien job sent <id_or_name_or_index>` sends the Markdown to the Hub.
7. The Hub sanitizes again and writes the runbook to the selected provider.
8. GitHub or Gitea build the wiki through the chosen mode; on local disk, the
   portal serves the file without writing to the volume.
9. `lucien runbook revise <uuid>` corrects an already published runbook, on any
   provider. Admins and domain seniors ask the Hub for a new immutable revision;
   the previous version stays for auditing.

!!! warning "Documentation is not a vault"
    Tokens, passwords, private keys, and credentials must never appear in
    runbooks. Use placeholders such as `SUA_SENHA_AQUI`, `SEU_USER_REDIS_AQUI`,
    and `SUA_KEY_EVOLUTION_AQUI`.

Read [Operation and security](operacao.md) before publishing a procedure. For
identity and authorization, see [IAM, RBAC, and metadata](iam-rbac.md).

Use the language selector in the header to switch between English and
Portuguese.
