# IAM, RBAC, and trusted metadata

## Authority

The Hub is the only identity authority. On every request the Bearer token is
converted to HMAC-SHA-256 with `AUTH_PEPPER`, looked up in the database, and
turned into a `SecurityContext` holding ID, username, level, function, and state.

The CLI neither sends nor persists its role or function. Revoked users get `401`
on the next request, because identity is looked up on every call.

## Bootstrap and administration

`POST /bootstrap/admin` is a controlled exception for creating the first admin.
It requires `USER_CREATION_ENABLED=true`, the bootstrap key, and the persistent
latch still open. Creating the admin and closing the latch happen in the same
transaction, so multiple workers or replicas cannot create two first admins. The
latch does not reopen if an admin is revoked. Disable the window after use.

Only an admin token may use:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/admin/users` | create a user and issue a provisional credential |
| `POST` | `/admin/users/{id_or_username}/provisional-token` | replace the credential with another provisional one |
| `PATCH` | `/admin/users/{id_or_username}` | change role and function |
| `DELETE` | `/admin/users/{id_or_username}` | revoke the user |
| `POST` | `/admin/users/{id_or_username}/reinstate` | reinstate a revoked user |
| `POST` | `/auth/exchange` | exchange a provisional credential for a permanent one |
| `GET` | `/me` | validate the token and read the current identity |

Admins cannot change or revoke their own identity through these endpoints.

## Issuing, rotating, and recovering tokens

The first administrator is created by `lucien create user`. The Hub returns a
permanent `luc_...` credential; the CLI shows it once and tries to save it in the
operating system account keyring. Then validate with `lucien auth status`. Do not
run that command inside a recorded session.

An authenticated administrator manages the following identities through the CLI:

```bash
lucien admin user create operador --role junior --domain servidores
lucien admin user update operador --role pleno --domain servidores
lucien admin user issue-provisional-token operador
lucien admin user revoke operador --yes
lucien admin user reinstate operador --yes
```

Creation shows a provisional `luc_tmp_...` credential, valid for four hours and
for a single exchange. Deliver it through a vault or an approved corporate
channel. The user runs `lucien login` and pastes the value into the prompt
without echo. The Hub consumes the provisional credential atomically, issues a
permanent one, and the CLI shows it once and saves it locally. The CLI uses
`Idempotency-Key`: if the response is lost, it repeats the same exchange and gets
the same permanent credential. A different key gets `401`.

`issue-provisional-token` repeats the flow when the permanent credential is lost.
Issuing one immediately invalidates the previous permanent credential and any
provisional one still pending. Do not repeat the command automatically: every
intentional run creates a new credential and makes the previous one unusable.
Responses use `Cache-Control: no-store`; the Hub keeps only the HMAC and the
expiry.

The Hub refuses to leave the administrator set empty: the last active admin
cannot be revoked or demoted, and the refusal comes back as `409`. The check runs
inside the same transaction that writes the change, so two administrators
revoking each other at the same time do not both succeed — one writes, the other
gets the conflict. Demoting counts as much as revoking: both remove an admin.

Losing the last administrator credential does not reopen the bootstrap. An
operator with administrative access to the host recovers that admin directly in
the container:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec hub python -m app.recover_admin Admin
```

The command requires an active admin, accepts a UUID or a username, and issues a
four-hour provisional credential. Role, domain, jobs, and publications are left
untouched. Use it immediately with `lucien login`. The operation records
`user.recover_provisional_token` without recording the credential.

`secrets/auth_pepper` must stay stable and protected by backup. Changing it
invalidates every existing token; in that case, recover the first admin offline
and use it to rotate the remaining credentials.

## Personal terminal and jump server

On a personal workstation, `lucien login` stores the credential in the local
account keyring. On a jump server, each person must have their own Unix account
and run the same login under that account. If the keyring is not available, the
file fallback may only be enabled on a controlled host; it uses a `0700`
directory and a `0600` file.

Do not use a shared Unix account: in that model users would also share the
profile, the vault, and the drafts, so there is no trustworthy isolation. The
local username is informational only; every remote call revalidates the
credential with the Hub, which determines the identity and the runbook metadata.

In the jump server automated mode, a separate M2M credential holds only the
`jump_enrollment` scope. It reaches neither jobs nor administrative routes. The
Hub correlates the POSIX ID (`U000001`, for example) with the Lucien username,
always creates new users as `pleno`, and accepts only `acessos`, `servidores`,
`redes`, or `suporte`. The helper never changes the role or domain of an existing
identity: `junior`, `pleno`, and `senior` keep their scopes. `admin` accounts use
the administrative login exclusively and are never activated over M2M. The
provisional token reaches the CLI through `stdin`; it appears in no argument,
environment variable, shell file, or log.

This policy is opt-in and exclusive to the jump host: only
`LUCIEN_JUMP_MODE=true` enables correlation with the POSIX account. Personal
workstations, WSL, and other hosts keep using `lucien login` and the individual
token, with no M2M and no LDAP dependency, preserving the original distributed
mode.

In jump mode the CLI compares the identity returned by `/me` with the expected
user and blocks `start`, `upload`, `reviews`, `job`, `admin`, and `create` on
failure. `stop` stays available locally so an already-started capture is not
lost. That block is an operational safeguard; the real authorization stays in the
Hub, on every request.

## Publication

The payload accepts exclusively `{"markdown": "REVIEWED_BODY"}`. Frontmatter in
the body, or extra identity fields, are rejected. The Hub sanitizes, validates
the grammar, applies RBAC, freezes the identity for idempotent retries, and
generates:

```yaml
---
id: "<job_id>"
autor: "<username_extracted_from_the_token>"
nivel_autor: "<role_level>"
funcao: "<domain_function>"
data_criacao: "<iso_8601>"
tags_inferidas: ["<tags_generated_by_the_SLM>"]
versao: "1"
ultimo_revisor: ""
data_revisao: ""
---
```

A revision adds `runbook_raiz`, `revisao`, and `substitui`, and fills the last two
fields with whoever published it and when. The four provenance fields (`autor`,
`nivel_autor`, `funcao`, `data_criacao`) are the **root's**, copied from the first
version: they describe the runbook, not the version. `funcao` in particular has to be
the root's, because it is what decides the destination directory — publishing the
reviser's area would make the document contradict the folder it sits in.

The frontmatter keys stay in Portuguese: they are part of the published document
format, read by the portal and by the wiki builder, and they are the same in
every installation regardless of interface language.

The SLM only infers tags. It never determines authorization. High criticality is
classified by deterministic rules over destructive commands; a `junior` user
cannot publish it.

In the local portal, `junior` and `pleno` stay read-only. `admin` may review any
runbook, and `senior` only documents whose trusted `funcao`, frozen in the
database, matches their current `domain_function`. The Hub does not use the
frontmatter presented by the portal as an authorization source.

## RBAC_ENTRY_ROLES_ENABLED

The restriction above is the default (`false`) and covers the two points where
entry-level roles are blocked:

| Operation | `false` (default) | `true` |
| --- | --- | --- |
| `junior` publishing high criticality | `403` | allowed |
| `junior` and `pleno` reviewing a publication | `403` | allowed, restricted to their own `domain_function` |

Released `junior` and `pleno` inherit the same domain restriction as `senior`:
they review only publications whose trusted `funcao` matches their own. Only
`admin` crosses domains, and the flag does not change that. Outside the
authorized domain the Hub keeps answering `404` instead of `403`, so as not to
confirm that the runbook exists.

The flag applies to the Hub and to the portal. In the portal it decides only
whether the edit button appears; authorization is re-evaluated by the Hub on
every revision, so a portal configured differently grants nothing. Keep the same
value in both to avoid a button that leads to `403`.

`RBAC_ENTRY_ROLES_ENABLED` is the only way for a `junior` to publish high
criticality. It does not change roles above, and it does not replace the
mandatory human review: the Markdown still goes through Secret Scanner, DLP, and
grammar validation.

A revision never overwrites the publication. The Hub creates another job and
appends to the server-side frontmatter:

```yaml
runbook_raiz: "<id-of-the-initial-publication>"
revisao: 2
substitui: "<id-of-the-previous-version>"
```

The same rules apply in the local portal and in `lucien runbook revise <uuid>`,
which serves all three providers. Role and domain are re-evaluated by the Hub
both when reading the body and when writing the revision, so no client bypasses
authorization by downloading the Markdown through another path.

## Configurable domain functions

`RUNBOOK_DOMAIN_FUNCTIONS` defines which functions exist in the installation. The
default, when the variable is not declared, is `acessos,servidores,redes,suporte`
— the same list that used to be hard-coded, so an existing installation does not
lose domains when it updates.

The list governs three paths, and it matters that it is the same in all three:
the `-r` of `lucien start`, user creation by an admin, and jump server
enrollment. If a user could be created in a domain outside it, their implicit
publication would land in a directory the administrator never declared.

The word "role" carries two meanings in the project, and they are worth
separating. In `lucien start -r`, **role is the area** — what this list
configures, and what becomes a directory. In `RBAC_ENTRY_ROLES_ENABLED` and in
the `RoleLevel` type, "role" is the **permission level**. Neither one is the
person's job title: Lucien does not model job titles, and the relationship
between someone's title and their level in the Hub is the organization's
decision.

Permission levels are not part of this list. `junior`, `pleno`, `senior`, and
`admin` stay hard-coded because each carries its own authorization rule — junior
does not publish high criticality, senior reviews only its own domain, admin
crosses domains. Making them configurable would require describing those rules in
configuration, which would move a security decision outside reviewed code.

The jump server script has its own copy of the list and **must be adjusted by
hand** when `RUNBOOK_DOMAIN_FUNCTIONS` changes; the Hub refuses what it does not
recognize, so a divergence shows up as an enrollment error, not as improper
access.

## Author name in the published runbook

The frontmatter identifies the author in the mixed format
`U000004 - Example Demonstration Operator Jr.`. The full name comes from the GECOS
field of the POSIX account, which SSSD fills from LDAP — no new lookup and no new
credential are involved.

The username stays **in the same field**, and on purpose: it is the identity that
auditing and RBAC know. Replacing it with the full name would make the document
more readable at the cost of traceability, and a name is not unique.

With no name in LDAP — or for a user created by an admin — the field shows only
the username, as before.

The GECOS is trimmed in the jump server script, which knows the format:
`Full Name,room,phone,phone`. Sending the whole field would put a phone number
and a room into the published runbook. The Hub sanitizes again — collapses
whitespace, removes control characters, and caps at 120 characters — but it would
have no way of knowing the fourth field was a phone number.

The name is **display only**: no authorization decision consults it. It arrives
in the enrollment payload, and the Hub treats it as published content, not as
identity.

A name change in LDAP propagates on its own: enrollment runs on every jump login
and updates the field.

## One operator in more than one area

A user has a **primary area** (`domain_function`) and, optionally, **additional
areas** granted by the admin. The primary one is the destination when
`lucien start` runs without `-r`, and it is the one that appears in the
frontmatter by default. `-r` accepts any area the user holds.

```bash
lucien admin user update U000004 -r servidores,acessos
```

The first in the list becomes the primary; the rest become additional. The list
**replaces** the whole set rather than adding to it: revoking an area means
omitting it from a new command. Every area goes through the same check against
`RUNBOOK_DOMAIN_FUNCTIONS` — granting an undeclared area would create a directory
the administrator never planned for.

The principle has not changed: an area is still a scope of authority, not a
preference. What changed is that the authorization can cover more than one area.
Whoever was not authorized still gets `403` on publication and `404` on revision.

Revision follows publication. Whoever publishes in `acessos` also reviews
`acessos` runbooks: both operations write to the same directory and go through
the same Hub layers. Restricting only the revision would create the asymmetry of
someone creating a runbook and then being unable to correct it.

`admin` still crosses any area, regardless of what it granted itself.

## Permission level on the jump server

Automatic enrollment creates the user as **`pleno`**. The Hub decides this, not
the script: `deploy/jump/lucien-jump-enroll.py` sends only `username` and, when
the Hub asks for it, `domain_function` — never the level. If the script chose,
anyone holding the jump server service credential could grant themselves `admin`.
The script only checks what came back and refuses the identity if the level is
not `junior`, `pleno`, or `senior`.

`pleno` publishes, including high criticality, but does not review. Promoting to
`senior` is a human decision:

```bash
lucien admin user update U000004 --level senior
```

## Schema migration

The Hub applies pending migrations on startup. There is no longer a list to run
by hand, nor an order to remember: `backend/migrations/*.sql` are applied in
sequence, each in its own transaction, under a PostgreSQL session lock so two
replicas starting together do not apply the same one twice.

What has already run is kept in `schema_migrations`:

```bash
docker compose --env-file .env -f docker-compose.local.yml \
  exec postgres psql -U lucien -d lucien -c 'TABLE schema_migrations'
```

The `origem` column says how each version got in:

| origem | meaning |
| --- | --- |
| `aplicada` | the Hub executed the `.sql` file |
| `adotada` | the effect was already in the database; nothing was executed |
| `modelo` | new installation, created in one go from the model |

`adotada` is what happens on the first startup of an installation that applied the
files by hand. Each migration declares a marker — the column or the table it
creates — and the marker, not the record, is the authority. That is also what
repairs a crash between running a migration and recording it: on the next startup
the marker answers that it is already there, and the version is settled instead of
repeated.

A new installation does not run `001`: it presupposes the legacy `users` and
`jobs` tables and renames existing columns. In a database with no `users`, the
model creates the whole current schema and the twelve versions are born settled as
`modelo`.

### Before a maintenance window

Take the backup and prove that it restores (`scripts/backup-db.sh` and
`scripts/test-restore.sh`). `001` and `003` acquire `ACCESS EXCLUSIVE` on `users`
and `jobs` to install constraints without accepting partial state; on a database
with volume, estimate the duration in staging first.

Legacy users migrate as `junior`, by least privilege, and `002` closes the
bootstrap latch if any admin already exists. After the migration, when applicable,
open a short bootstrap window to create the first admin.

The files remain applicable by hand with `psql -f`, where the `BEGIN`/`COMMIT` in
each one provides atomicity. Through Hub startup, the runner controls the
transaction, and includes the version record in it.
