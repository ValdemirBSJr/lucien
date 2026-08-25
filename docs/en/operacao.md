# Operation and security

## Writing a runbook

A procedure must explain goal, prerequisites, impact, commands, validation, and
rollback. Destructive commands need an explicit confirmation and a stop
criterion.

Every step must follow exactly this grammar, with no blank line between the
heading and the block:

````markdown
### Step 1: List containers
```bash
docker ps
```
> Confirm that the expected containers are running.
````

The CLI uses `### Step X: Action`; earlier documents using
`### Passo X: Ação` remain valid. Steps must start at 1 and be sequential. The
Hub rejects loose `bash` blocks, steps with no command, and frontmatter sent by
the client.

Generic code blocks — `yaml`, `json`, or examples nested in ` ```` ` — are
treated as literal content: what is inside them takes no part in the step
grammar. Every opened fence must be closed; an unclosed block is rejected.

## Sanitization

The Hub applies the same policy at three boundaries:

- before sending the log to the SLM;
- over the commands the SLM returns, before creating the job;
- after the human review and before persisting the Markdown.

It neutralizes `Authorization` headers, credentials embedded in URLs, PEM private
keys, known tokens, the Redis `AUTH` command in command position (start of line,
after a Redis prompt ending in `>`, or on a line starting with `redis-cli`), and
variables or flags named after a password, token, secret, or key. Markdown
headings, blockquotes, and prose that merely mention the word "auth" are left
alone. The CLI reports how many substitutions were made on publication, without
recording the removed values.

Example:

```dotenv
REDIS_USER=SEU_USER_REDIS_AQUI
REDIS_PASSWORD=SUA_SENHA_REDIS_AQUI
EVOLUTION_API_KEY=SUA_KEY_EVOLUTION_AQUI
```

Those placeholders are the literal values the DLP writes, so they are the same in
every installation and are not translated.

!!! danger "The limit of this protection"
    Regular expressions reduce accidental exposure, but they replace neither DLP
    nor secret scanning. Positional credentials or proprietary formats can slip
    through; keep human review and a secret scanner as an independent barrier in
    the repository.

## Approval

Protect the `main` branch on the Git provider, require a Pull Request, and at
least one approval. The current storage provider uses the Contents API and writes
directly to `GIT_BRANCH`; that protection therefore requires an evolution to
create a branch and a Pull Request, or a narrowly limited service bypass.
Allowing unrestricted push contradicts the approval requirement.

## Upload queue and worker

The Hub accepts the upload before inference. Follow the consumer and the states
without logging payloads:

```bash
docker compose --env-file .env -f docker-compose.local.yml ps hub upload-worker slm
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 upload-worker
lucien job status <JOB_ID>
```

`UPSTREAM_ERROR` means the attempts against the SLM or the scanner are exhausted.
Fix the dependency and use `lucien job retry <JOB_ID>`. Do not retry in an
external loop: the worker already applies exponential backoff and a durable
lease.

You can raise `LUCIEN_SLM_CPU_LIMIT` up to the total granted to Docker and
recreate only `slm`. `upload-worker` replicas are safe thanks to `SKIP LOCKED`,
but they do not increase throughput when they all compete for a single SLM
limited to one inference at a time.

If the CPU limit is lower than the host's total CPUs, set `SLM_NUM_THREAD` to
match it. Ollama sizes its threads from the host and ignores the cgroup quota;
excess threads spend the period spinning, the container is throttled, and every
token gets more expensive.

## When the SLM cannot sustain enrichment

An upload makes two calls to the SLM: command extraction, which is
irreplaceable, and enrichment, which is auxiliary. On a slow host, the second one
is the first to overrun `SLM_TIMEOUT_SECONDS`. Three ways out, from the most
targeted to the broadest:

- `lucien upload -s` or `lucien job retry <JOB_ID> -s` skip enrichment for that
  job. The draft comes out with the basic structure, and the operator writes the
  goal, validation, and rollback during review.
- `SLM_ENRICHMENT_ENABLED=false` applies the same effect to every upload.
- `RUNBOOK_ENRICHER=deterministic` keeps enrichment, but without a model: tags,
  prerequisites, impacts, and rollback come from reviewable tables. It is
  instantaneous and cannot hallucinate, but it covers only the commands those
  tables know.

Even when enabled, an upstream failure in enrichment does not bring the job down:
the worker records `job.enrichment_skipped` and preserves the completed
extraction. Extraction still depends on the SLM in every case above.

Measure the real rate before blaming the model. Model size only matters if
generation scales with it: if a model six times smaller yields the same number of
tokens per second, the model is not the bottleneck, and swapping in a smaller one
will not help.

A `PROCESSING` job whose work should be abandoned can be canceled by its owner
with `lucien job del <JOB_ID> --force`. The worker treats concurrent removal as an
expected cancellation; `PUBLISHED` stays immutable.

!!! warning "Rotating AUTH_PEPPER"
    The same root derives token HMACs and the queue's AES-GCM key. Before
    rotating it, wait for `PROCESSING` jobs to finish and resolve or purge
    `FAILED` jobs. Payloads encrypted with the previous key cannot be recovered
    after the rotation; that is intentional, and it avoids an insecure fallback.

## Sizing the secret-scanner

Each scan is a gitleaks subprocess. Two variables limit how many exist at once
and how long a request waits for a slot:

| Variable | Effect | Default |
|---|---|---|
| `SCANNER_MAX_CONCURRENCY` | concurrent processes | `4` |
| `SCANNER_QUEUE_TIMEOUT_SECONDS` | maximum wait for a slot | `10` |

The limit matters more than it looks. The Hub policy is **fail-closed**: an
unavailable scanner blocks publication. A batch of uploads that exhausts the
container does not hurt only itself — it stops publication for the entire
installation.

When saturated, the service answers `503` instead of queueing without limit. The
Hub treats that as an upstream failure and reschedules the job with backoff,
which is better than leaving connections hanging until they blow up somewhere
else.

To size it, start from the CPUs granted to the container. Gitleaks is CPU-bound,
so more processes than cores only trades throughput for context switching:

```
SCANNER_MAX_CONCURRENCY  ≈  container CPUs
```

Observe under real load before increasing it:

```bash
docker compose --env-file .env -f docker-compose.local.yml exec secret-scanner sh -c 'ps -o pid,etime,comm | grep -c gitleaks'
```

```bash
docker stats --no-stream lucien-runbook-secret-scanner-1
```

If `503` responses appear with "saturado" in the worker log and the process count
stays pinned at the ceiling, increase it. If memory climbs without throughput
following, reduce it.

The `pids_limit: 64` in Compose is the last barrier, not the first: the
application semaphore is what should hold. The container limit exists in case the
semaphore is defective, and it takes down the service instead of the host.

### Readiness separated from liveness

`/health` answers that the process is up. `/ready` verifies that gitleaks can be
executed, running `version` — with no input and no finding, so nothing sensitive
passes through the process or the log.

The separation matters because a live service with a broken binary would accept
requests and answer `503` to all of them. Being fail-closed, that would stop
publication without the orchestrator taking the replica out of rotation. The
Compose healthcheck queries `/ready`.

## Sizing the SLM context window

Two variables govern how much the SLM reads per job, and both depend on the
hardware:

| Variable | Effect | Default |
|---|---|---|
| `SLM_NUM_CTX` | context window requested from the runtime | `8192` |
| `SLM_PROMPT_MAX_CHARS` | ceiling of the reduced log sent to the model | `8000` |

### Why `SLM_NUM_CTX` must be explicit

Without that variable the runtime uses its own default — **2048 tokens** — and
silently discards whatever does not fit. The cut comes from the start of the
prompt, which is exactly where the instruction to answer with JSON only lives.
The model then answers anything at all and the job fails with `UPSTREAM_ERROR`,
with nothing in the log pointing at truncation.

The symptom is recognizable: `prompt_eval_count` stuck at ~2050 even with a large
log. To measure:

```bash
docker compose --env-file .env -f docker-compose.local.yml exec slm \
  sh -c 'ollama ps'
```

Note that `ollama show <model> | grep context` reports what the **model
supports**, not what the runtime **allocates**. The two numbers are different, and
only the second one matters here.

### How much memory it costs

The window reserves a cache proportional to its size. The rule of thumb:

```
cache memory ≈ 2 × layers × dim_kv × num_ctx × 2 bytes
```

Rather than estimating, measure: start with the intended value and watch the
container.

```bash
docker compose --env-file .env -f docker-compose.local.yml exec slm ollama ps
```

```bash
docker stats --no-stream lucien-runbook-slm-1
```

The `SIZE` column of `ollama ps` already includes the cache for the configured
window. If it gets close to the container memory limit, reduce `SLM_NUM_CTX`.

### How to choose the two values

Start from the prompt, not from the window. The Hub already collapses output
blocks before sending — a packet-capture session with hundreds of lines usually
arrives as a few hundred characters. `SLM_PROMPT_MAX_CHARS` is the final ceiling,
for the case of a session with many commands.

Convert the ceiling into tokens by dividing by three, a conservative
approximation for terminal text, and add slack for the system prompt and the
answer:

```
SLM_NUM_CTX  ≥  (SLM_PROMPT_MAX_CHARS ÷ 3)  +  512
```

With the defaults: 8000 ÷ 3 ≈ 2700, plus 512, gives ~3200. The default of 8192
leaves comfortable slack and is still modest in memory.

Three situations call for adjustment:

**Host with little memory.** Reduce both together, keeping the relation above:
`SLM_PROMPT_MAX_CHARS=3000` with `SLM_NUM_CTX=2048`, for example.

**Slow host.** The time of each attempt grows with the number of prompt tokens.
Reducing `SLM_PROMPT_MAX_CHARS` makes each attempt cheaper without losing a
command — the reduction preserves every command line and only shrinks the output.

**Very long sessions.** If the operator tends to capture dozens of commands,
raising `SLM_PROMPT_MAX_CHARS` gives the model more context. Raise `SLM_NUM_CTX`
along with it, by the formula, or the silent truncation comes back.

### What these values do not change

The reduction applies only to the model prompt. The observed-command filter, the
per-prompt recall pass, and output extraction still receive the complete log. A
poorly chosen value can worsen the ordering the SLM suggests, but it makes no
command disappear from the runbook and does not change the published output
blocks.

## Backup and recovery

The database holds identity, RBAC, the encrypted queue, and the lineage of
publications. The published artifacts live outside it — in Git or in the volume —
and survive the loss of the database, but they become orphans: they stay readable
and do not appear in the catalog, because the catalog is a database query.

### Generating

```bash
BACKUP_DIR=/mnt/backup scripts/backup-db.sh
```

The script produces a dump in custom format, **verifies that the file can be
read**, and only then applies retention. The order matters: deleting the old ones
before validating the new one would leave a window with no copy at all.

| Variable | Effect | Default |
|---|---|---|
| `BACKUP_DIR` | destination of the copies | `./backups` |
| `BACKUP_RETENTION` | how many to keep; `0` disables removal | `14` |
| `BACKUP_ENCRYPT_KEY_FILE` | file holding the encryption passphrase | empty |

Without `BACKUP_ENCRYPT_KEY_FILE` the dump stays in clear text, protected only by
filesystem permissions — the directory is born `0700` and the file `0600`, and
the script warns about it. The dump carries credential hashes and the encrypted
queue; treat it as sensitive material regardless of the option chosen.

### Proving the copy works

A copy that was never restored is a hypothesis, not a recovery plan.

```bash
scripts/test-restore.sh
```

With no argument, it uses the most recent one. The test starts a throwaway
PostgreSQL **with no network**, restores there, and checks that the identity,
job, and queue tables arrived, that at least one active admin exists, and that
the constraints came along. Nothing touches the installation.

A restored Hub with no active admin cannot be administered: the recovery would be
incomplete even with every table present. That is why this check exists.

### Restoring in production

```bash
scripts/restore-db.sh /mnt/backup/lucien-20260820T210000Z.dump
```

**Destructive operation.** It requires typing `RESTORE`, and it stops the Hub and
the worker first — restoring while they write produces a state that matches
neither the copy nor what was there. If the restore fails, both stay stopped on
purpose: coming up over a half-restored database is worse than being down.

Prove the copy with `test-restore.sh` **before** using this command.

### Objectives and responsibilities

| | |
|---|---|
| **RPO** | equal to the interval between `backup-db.sh` runs. With no schedule, it is the time since the last manual run. |
| **RTO** | minutes: stop the services, restore, and start. Dominated by the dump size. |
| **Owner** | whoever operates the installation. There is no scheduling automation and no remote destination. |
| **Scope** | the database only. Published artifacts depend on the backup of Git or of the `playbooks-data` volume. |

To reduce the RPO, schedule the script on the host — a daily cron entry, for
example. The project does not install that schedule because the frequency is a
decision for whoever operates it, and a silent cron that fails is worse than no
cron at all.

### Evidence of the last test

Record here on every `test-restore.sh` run, so the date is verifiable rather than
a memory:

| Date | File | Result |
|---|---|---|
| _(to fill in)_ | | |

## Quality gates before publishing

Deployment is manual: the files are copied to the server by hand. A CI that fires
on push does not protect that moment, so the same gates exist as a script, to run
before the copy.

```bash
bash scripts/verify.sh
```

It runs eleven gates — test images for the backend, the portal, the wiki-builder,
and the secret-scanner; migrations against a throwaway PostgreSQL; `gofmt`,
`go vet`, and `go test` for the CLI; syntax and ShellCheck for the scripts;
`docker compose config` plus the network segmentation check; `mkdocs --strict`;
Ruff and mypy over the Python. Each runs to the end; the verdict comes after all
of them, so a single run shows everything that needs fixing.

To work on a specific gate:

```bash
bash scripts/verify.sh backend
```

A gate that depends on a tool missing from the machine — `go`, `mkdocs` — is
marked as skipped, not as passed. A missing tool is not evidence that the code is
good.

The same gates live in `.gitea/workflows/ci.yml` and in the GitHub mirror, for
when a runner exists. The script is the source of truth for the commands, so the
two do not diverge.

## Checking the deployment after bringing it up

`verify.sh` validates the **code**. What runs on the server is a different thing,
and the two diverge silently: in a single night `docker-compose.local.yml`
predated the network segmentation, an image had lost a file the service reads
every cycle, and one service's source on the server was weeks older than the
repository — masked by a healthcheck pointing at a route that existed in both
versions. None of the eleven gates saw anything, because none of them looks at
what is live.

Run this on the Hub host, from the installation root, after any deployment:

```bash
bash scripts/verify-deploy.sh
```

It compares five things against the repository:

| Check | What it catches |
| --- | --- |
| `docker-compose.local.yml` identical to the base | configuration that "went up" but was never regenerated |
| marker in each service's source | a file that was never copied to the server |
| live route in the running image | an old image running with new source on disk |
| `schema_migrations` complete | a missing migration |
| a real outbound connection | segmentation that exists in the file and not in reality |

The last two lines are measured, not read. Asking the Compose file whether the
database has internet access is exactly what let the problem through — it was
right in the repository and wrong on the machine. The script opens the
connection.

A service that is down becomes a warning, not a failure: a disabled profile is
not a divergence. A failure is where the repository and the machine disagree.

When a new service arrives, add its marker to `conferir_fonte`. A marker is any
symbol that exists only in the current version of the file.

## Diagnosing the wiki builder

A `wiki-builder` that is `Up` and `unhealthy` means no cycle completed in the
last few minutes. The healthcheck requires a recent publication **and** the
`current` link; if it never existed, the builder never promoted a release and
`wiki-static` is serving the default Nginx page, not stale content.

```bash
docker compose --env-file .env -f docker-compose.local.yml exec wiki-builder ls -la /publish
```

Look for `current`. For the cause, group the log instead of reading it line by
line:

```bash
docker compose --env-file .env -f docker-compose.local.yml logs --tail=2000 wiki-builder | grep -oP 'preservada: \K.*' | sort | uniq -c | sort -rn
```

Note that `restart: unless-stopped` does **not** react to `unhealthy` — only Swarm
does. A failing builder stays up indefinitely, so it is worth including it in the
periodic check instead of trusting the automatic restart.

## Local runbooks portal

With the `local-viewer` preset, open `https://<hub-host>:9091` and authenticate
with the same username and personal token the CLI uses. A username alone is not a
credential. The portal revalidates the token with the Hub on every page, renders
only valid files from the volume, and never writes to them directly.

`junior` and `pleno` users are read-only. A `senior` can open **Edit** only on
runbooks in their own domain; an `admin` can review any local runbook. The
effective decision happens again in the Hub. Every save creates an immutable
revision with a new ID and keeps the previous version for auditing.

The theme follows the system light/dark preference on the first visit. The header
button stores only the visual choice in `localStorage`; identity, token, and
content are not stored there. To diagnose without revealing credentials:

```sh
docker compose --env-file .env -f docker-compose.local.yml ps runbook-viewer
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 runbook-viewer
```

Do not publish 9091 to the Internet. Restrict the port to reader networks and back
up the `playbooks-data` volume with a tool that preserves POSIX permissions. The
portal is stateless; its replicas may share the same read-only volume and the same
session secret.

The editor guards concurrency with `If-Match`. If you get `412`, someone else has
already published a revision: reload and reapply your change on top of the current
version. On a `502` failure, do not reopen the editor; retry on the same page to
preserve the idempotency key and the attempted body. The interface returns `403`
when the visible role or domain does not allow editing. If someone calls the Hub
API directly, a `senior` from another domain gets `404`, so as not to confirm that
the runbook exists. Never work around those responses by changing the frontmatter:
file metadata grants no privilege.

## Operating compact Gitea

The builder polls the branch and only promotes a complete release. A clone or
build failure does not remove `current`; fix the cause and let the retry process
the same commit. Follow both services without touching PostgreSQL or the SLM:

```sh
docker compose --env-file .env -f docker-compose.local.yml ps wiki-builder wiki-static
docker compose --env-file .env -f docker-compose.local.yml logs --tail=100 wiki-builder
```

`wiki-volume-init` must finish with `Exited (0)`: it adjusts only the ownership
and modes of the volumes for UID `10004` before the builder and Nginx start. The
job has no network and keeps only `CHOWN` and `FOWNER` while it runs. The builder
stays non-root and without capabilities. If an earlier installation logs
`Permission denied: '/publish/.builder.lock'`, update Compose and recreate the
compact-mode services:

```sh
docker compose --env-file .env -f docker-compose.local.yml \
  -f docker-compose.build.yml build wiki-builder
docker compose --env-file .env -f docker-compose.local.yml \
  up -d --force-recreate wiki-volume-init wiki-builder wiki-static
```

The compact Nginx listens on `127.0.0.1:9092` by default. Use a managed HTTPS
proxy for remote access. Do not mount the Docker socket, and do not replace the
fixed MkDocs configuration with files from the repository.
