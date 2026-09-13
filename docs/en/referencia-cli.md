# Command reference — `lucien-cli`

A guide to every command, argument, and flag in the CLI. Generated from the source in `cli/cmd/`.

No persistent flag is defined at the root — Cobra adds `-h, --help` to every command automatically, and `--version` at the root.

Two environment variables change behaviour without being flags: `LUCIEN_JUMP_MODE=true` combined with `LUCIEN_EXPECTED_USERNAME` forces an identity check before `start`, `upload`, `reviews`, `job`, `admin`, and `create` run.

## `lucien auth`

A group with no action of its own — use one of the subcommands.

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien auth status` | — | — | Validates the stored token and shows the Hub identity: user, ID, permission level, and every authorised area. It also enforces `LUCIEN_EXPECTED_USERNAME` when that variable is set. |
| `lucien auth ensure` | — | — | Silently validates the stored credential, or starts the interactive login flow when there is no valid one. Meant for non-interactive use and scripts. |

## `lucien admin`

Identity management on the Hub — authorisation is always enforced by the Hub, never by the client.

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien admin user create <name>` | `name` (required) | `--level <string>` (required) — permission level: `junior`, `pleno`, `senior`, or `admin`.<br>`-r, --role <string>` — comma-separated areas; the first is the primary one (the default `start` uses when `-r` is omitted). | Creates a user and displays a four-hour provisional token **once**. The new user must run `lucien login` within that window. |
| `lucien admin user issue-provisional-token <user-id-or-name>` (alias: `rotate-token`) | `user-id-or-name` (required) | — | Invalidates the user's permanent token and issues a new four-hour provisional one. |
| `lucien admin user update <user-id-or-name>` | `user-id-or-name` (required) | `--level <string>` — new permission level.<br>`-r, --role <string>` — comma-separated areas that **replace** the current set; the first is the primary one. | Updates a user's level and/or areas. At least one of the two flags is required. Omitting `-r` preserves the current areas; passing it replaces the whole set. |
| `lucien admin user revoke <user-id-or-name>` | `user-id-or-name` (required) | `--yes` (bool, default `false`, required) — confirms revoking the user. | Revokes the user's token immediately. Without `--yes` the command refuses to run. |
| `lucien admin user reinstate <user-id-or-name>` | `user-id-or-name` (required) | `--yes` (bool, default `false`, required) — confirms reinstating the user. | Reactivates a revoked user and issues a four-hour provisional token. Without `--yes` the command refuses to run. |

## `lucien login`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien login` | — | `--token-stdin` (bool, default `false`) — reads the token from stdin, keeping it out of the process arguments.<br>`--quiet` (bool, default `false`) — suppresses the success output, for controlled automation. | Stores a newly issued Hub token for the current operating-system user. Accepts a provisional or a permanent token; provisional ones are exchanged for a permanent one and the profile is saved locally. |

## `lucien create`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien create user <name>` | `name` (required) | — | Creates the **first** administrator through bootstrap and activates the local profile. Requires the `LUCIEN_BOOTSTRAP_KEY` environment variable. Single use, to initialise the first admin — after running it, do not use `lucien login`. |

## `lucien reviews`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien reviews` | — | — | Lists the active user's jobs awaiting completion (ID, NAME, STATUS, CREATED AT). The numeric index shown can be used as the `<review_index>` shortcut in the `job` subcommands. |

## `lucien job`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien job <id_or_name_or_review_index>` | 1 positional (required): UUID, name, or the numeric index from `reviews` | `--reset` (bool, default `false`) — discards the saved draft and starts again from the template. | Selects commands and opens the playbook in `$EDITOR`. Works only on `PENDING` jobs. Without `--reset`, it resumes an existing local draft. Opens an interactive multi-select of the captured commands, assembles a Markdown template, opens `$EDITOR`, and saves the result as a local draft (mode 0600). |
| `lucien job cat <job_id>` | 1 positional (required): the **exact UUID** — name and index do not work here | — | Prints the saved draft without opening the editor. It reads only the local draft and never contacts the Hub — useful when a draft was refused at publication. Refuses to run inside a recorded session, so secrets do not leak into the recorded log. Output goes to stdout, ready to pipe. |
| `lucien job sent <id_or_name_or_review_index>` | 1 positional (required) | — | Publishes the reviewed draft. Loads the local draft, computes an idempotency key (sha256 of user + job + content), and publishes to the Hub. Deletes the local draft on success; warns when the Hub sanitised (redacted) sensitive values during publication. |
| `lucien job del <id_or_name_or_review_index>` | 1 positional (required) | `-y, --yes` (bool, default `false`) — skips the confirmation.<br>`-f, --force` (bool, default `false`) — cancels and deletes a `PROCESSING` job; it never deletes a `PUBLISHED` one. | Deletes a `PENDING` or `FAILED` job; with `--force`, it also cancels a `PROCESSING` one. Asks for confirmation unless `--yes` is passed. It also removes any local draft. |
| `lucien job status <id_or_name_or_review_index>` | 1 positional (required) | — | Shows the asynchronous processing status: ID, Name, Status, and Error when there is one. Use it to follow up after `upload` or `retry`. |
| `lucien job retry <id_or_name_or_review_index>` | 1 positional (required) | `-s, --skip-enrichment` (bool, default `false`) — reprocesses without the SLM enrichment step; when omitted, it keeps the choice made at the original upload. | Reprocesses a `FAILED` job (the only accepted status). Requeues the job and prints the status command to follow it. |

## `lucien runbook`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien runbook revise <published_runbook_uuid>` | 1 positional (required): the **exact UUID** of the published runbook — no index, no name | — | Revises a published runbook, creating an immutable successor. Downloads the published body, opens it in `$EDITOR`, and sends the result back to the Hub, which creates a new immutable version preserving the lineage. It becomes a no-op ("No changes detected; revision cancelled.") when the edited content matches the original. Uses optimistic concurrency through a content hash and an idempotency key. |
| `lucien runbook cat <published_runbook_uuid>` | 1 positional (required): the **exact UUID** of the published runbook — no index, no name | — | Prints the body of a published runbook without opening the editor. Same relation to `revise` that `job cat` has to `job`: read what is there without the risk of editing it. Unlike `job cat`, this one does query the Hub — a published runbook exists only there, and it already passed the secret policy and the DLP before being published. Requires the exact UUID, like `revise`. Refuses to run inside a recorded session, so it does not pollute the capture. Output goes to stdout, ready to pipe. |
| `lucien runbook list` | — | — | Lists the published runbooks the user reaches through their areas — all of them, the primary and the extra ones; an admin sees every area. Shows NAME, ID, AREA, and PUBLISHED AT, **current version only**: a revision creates a new version with its own UUID, and `revise` refuses the older one with a conflict. Read-only: it changes nothing. The ID column is what `runbook cat` and `runbook revise` take. Being on the list does not waive the level: `revise` still requires `senior` or `admin` (or `RBAC_ENTRY_ROLES_ENABLED=true`). The table goes to stdout and the usage hint to stderr, so it can be filtered through a pipe. |

## `lucien start`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien start <provider_name>` | `provider_name` (required) | `-d, --describe <string>` — short task description (recommended; it improves SLM accuracy); at most 280 characters, trimmed; warns when omitted.<br>`-r, --role <string>` — area to publish under (not a permission level; defaults to the user's own). Must match `^[a-z][a-z0-9_]{2,63}$` when given.<br>`-y, --yes` (bool, default `false`) — discards a stopped, never-uploaded session without asking, so the new capture can start. | Opens a PTY and records stdin/stdout locally. The session must then be ended with `stop` and sent with `upload`. When a stopped, never-uploaded session already exists, it warns and asks for confirmation before overwriting it (`lucien session cat`, `edit`, or `discard` resolve it without losing the recording; `--yes` skips the question). A session still `RUNNING` is never overwritten — `start` refuses until `stop` ends it. |

**Careful:** `-r/--role` in `start` and in `admin user create/update` means **area** (the domain function, for example a group of servers or of network devices) — not a permission level. The permission level is the separate `--level` flag in `admin user create/update` (`junior`, `pleno`, `senior`, `admin`).

## `lucien stop`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien stop` | — | — | Stops the PTY and preserves the session locally. It can be run inside the recorded terminal (invoked automatically) or from another terminal. Warns when the local log reached its size limit and was truncated. |

## `lucien upload`

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien upload` | — | `-s, --skip-enrichment` (bool, default `false`) — skips the SLM enrichment step; the draft keeps only the extracted commands. | Uploads the pending stopped session to the Hub (the log is already sanitised). On a timeout it reconciles by looking the job up under the session's generated name. Cleans the local session files on success; reports `Job_ID` and status, along with the follow-up command `lucien job status <id>`. |

## `lucien session`

A session stays on disk from `lucien stop` until the Hub accepts it. When the upload is refused — by the secret policy, for example — it stays there indefinitely, because the automatic cleanup only runs after an accepted upload. These commands are how you look at it, fix it, and get rid of it.

| Command | Arguments | Flags | Description |
| --- | --- | --- | --- |
| `lucien session cat` | — | — | Prints the recorded session with the ANSI sequences stripped — byte for byte what `lucien upload` would send to the Hub. It is the recording, not a list of extracted commands: separating command from output is the Hub's job, and its grammar covers both POSIX shells and network equipment CLIs — a second grammar here would drift from that one, and a filtered view that disagrees with what the Hub actually reads is worse than no view. Refuses to run inside a recorded session, for the same reason as `lucien job cat`. Output goes to stdout, ready to pipe or grep. |
| `lucien session edit` | — | — | Opens the recorded session in `$EDITOR` and saves what you write back; `lucien upload` reads the log from disk on every attempt, so the next one sends the corrected text. This is the way out of a refusal that would otherwise cost the whole recording. What you edit is the recording — the evidence the published runbook is built from; removing a secret is what this is for, while rewriting output the equipment returned defeats the purpose of recording it. The Hub scans again on the next upload, so editing does not get past the gate; it only lets you reach the review step you were already entitled to. Refuses to run inside a recorded session. |
| `lucien session discard` | — | `-y, --yes` (bool, default `false`) — skips the confirmation. | Permanently removes the session state and the log file from this machine. Nothing is sent to the Hub — a refused session never created a job there, so there is nothing to delete on the other side. Use it after an upload refused by the secret policy: the refusal keeps the secret out of the Hub, but the recording that contains it stays on this disk until you remove it. |

## The numeric review index

Several `job` subcommands accept a `<review_index>` in place of the UUID or name: it is the number shown by `lucien reviews`, resolved internally against the user's list of active jobs (1-based).
