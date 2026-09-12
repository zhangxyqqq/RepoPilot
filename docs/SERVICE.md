# RepoPilot execution service

This milestone adds a production-style control plane around the existing single-agent
runtime. It is a bounded, single-host service, with deterministic PostgreSQL and
Docker tests. Bounded local throughput measurements are recorded separately in
[SERVICE_STRESS.md](SERVICE_STRESS.md); they establish no production capacity,
availability, or broad solve-rate claim. Historical evaluation evidence remains independent.

## Architecture and ownership

```mermaid
flowchart TD
    Client[Client with bearer credential] --> API[FastAPI v1 REST API]
    API --> Service[Task service]
    Service --> DB[(PostgreSQL tasks and runs)]
    DB --> Claim[Transactional SKIP LOCKED claim]
    Claim --> W1[Trusted worker 1]
    Claim --> W2[Trusted worker 2]
    W1 --> Adapter[Internal run_agent adapter]
    W2 --> Adapter
    Adapter --> Loop[Existing AgentLoop and RecoveryPolicy]
    Loop --> Tools[Existing ToolRegistry / ToolBackend]
    Tools --> Sandbox[Restricted Docker staged copy]
    Loop --> Trace[Existing trajectory and run.json]
    Trace --> Artifacts[Shared host artifact directory]
    Adapter --> Result[Redacted result and trace summary]
    Result --> DB
```

The API does validation and short database transactions; it never starts an agent
or waits for execution. Workers call the internal `run_agent` function with a
persisted run UUID. They neither shell out to the CLI nor duplicate controller
logic. CLI, local stdio MCP, benchmark/evaluation profiles, and the recovery policy
remain authoritative and reusable. The service uses the normal direct tool path;
it does not introduce remote MCP or additional tools.

`Task` represents the user request. `Run` represents an individual attempt, with
worker identity, claim/start time, heartbeat/lease, terminal time, trace identity,
stop reason, error code, artifact reference and result JSON. Retried tasks preserve
old attempts. Provider/model are resolved at submission from operator configuration;
clients cannot submit provider endpoints, keys, Docker settings, test commands or
arbitrary executable configuration. The current service supports `openai` and
`deepseek`; the existing CLI retains its additional compatible-endpoint workflow.

## Transactional lifecycle

```text
Task: QUEUED -> RUNNING -> SUCCEEDED | FAILED
Run:            RUNNING -> SUCCEEDED | FAILED | ABANDONED
```

On recovery, the task stays RUNNING, its old run becomes ABANDONED, and a new run
is created in the same transaction. The latest-run foreign key must reference a
run belonging to that task. A partial unique index permits only one RUNNING run
per task, and `(task_id, attempt)` is unique. Database checks constrain statuses
and terminal timestamps; triggers reject invalid transitions, including terminal
resurrection. The service exposes no arbitrary status-update API.

Claiming uses PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`, taking one candidate
row at a time and committing before any agent work. Independent workers can skip
locked candidates. Heartbeats and completions require the expected run ID, worker
ID, RUNNING status and an unexpired database-clock lease. Completion also locks the
task and verifies its latest run. Rejected late completions cannot overwrite a
new attempt. See the PostgreSQL [locking clause documentation](https://www.postgresql.org/docs/17/sql-select.html)
for the queue-consumer use of SKIP LOCKED.

Migrations are explicit Alembic revisions, separate from application startup.
Readiness checks the database and expected migration revision. Schema creation
never silently falls back to SQLite or an in-memory store. Application connections
have connect, statement and lock timeouts. The API uses a bounded eight-connection
Psycopg pool, each worker a two-connection pool; acquisition waits at most three
seconds, and connections are checked before reuse. Pools close with process/app
lifetimes and transactions finish before a connection returns to its pool; database failures yield safe 503 responses.
A single migration job runs before API/workers in Compose. See the
[Alembic migration environment documentation](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

## Three distinct reliability guarantees

1. **Idempotent client submission.** Optional `Idempotency-Key` is SHA-256 hashed
   for storage and uniquely constrained in PostgreSQL. A canonical fingerprint of
   the validated v1 request distinguishes matching replays from conflicts. Matching
   replays return the original task (200), including after restart or source removal;
   conflicting replays return 409. Concurrent inserts converge on one row. Missing
   keys create independent tasks. A newly created task returns its committed creation
   snapshot (202); GET retrieves newer state if a worker has already advanced it. Keys have no automatic expiry and are global to
   this single-principal service. Changing operator model settings does not change
   an existing key's task. Whitespace trimmed by the schema and the default profile
   normalize consistently; different repository path spellings remain different
   request payloads.
2. **No simultaneous execution of one task within the supported deployment.**
   PostgreSQL claims prevent competing initial owners. Each owner also acquires a
   nonblocking OS `flock` on `.locks/<task UUID>` in the shared artifact directory,
   holding it across setup, synchronous agent execution, Docker cleanup and result
   commit. Service Docker commands run under a small supervisor that inherits the
   same lock descriptor and owns the command timeout. If a host worker dies while
   its Docker CLI helper survives, that helper/supervisor still blocks replacement.
   The descriptor is never forwarded by Docker into the agent container. This is
   an interprocess kernel lock, not a Python mutex. A live worker
   that loses its DB connection, overruns its lease, or is paused still holds this
   lock, so another worker cannot start the same task. Other queued tasks remain
   eligible. Lock files must never be removed while the system is running: removing
   the inode could admit two independent locks. The lock directory is never mounted
   in an agent sandbox.
3. **Bounded recovery after process death.** The execution lock is released after
   the worker and any inheriting Docker helpers have exited. After lease expiry (default 30 seconds), another worker can claim a fresh
   attempt, up to three attempts by default. Before executing, it removes any old
   container found under the deterministic task-specific Docker name. Tool calls,
   close, and cleanup target immutable container IDs, so a delayed request cannot
   modify or delete a replacement that reuses the name. A failed cleanup
   blocks execution and leaves the attempt recoverable. At the attempt limit it
   performs cleanup then terminally fails the last attempt with `attempts_exhausted`,
   without another agent call. The worker identity on that final attempt identifies
   the cleanup owner. Normal agent failures and execution exceptions are terminal;
   automatic service retries are for interrupted/expired worker attempts only.

The worker heartbeats every lease/3 and revalidates the lease after cleanup before
authorizing agent execution. An observed heartbeat/DB failure suppresses result commit
and allows the current synchronous call to wind down while retaining the execution
lock. The worker watchdog exits the execution process after 900 seconds by default,
including a blocked claim and stalled setup/cleanup/provider calls. Docker command
supervisors keep their own timeout after host-worker death (the tool timeout where
specified, otherwise 60 seconds). Recovery may wait for that helper to exit before
acquiring the lock. A worker-container death also terminates its in-container helpers;
the next owner must still clean up the separately managed agent sandbox. The next owner performs orphan
cleanup. SIGTERM drains an in-flight claim/task and exits before the next dispatcher iteration; Compose allows 16
minutes before forced termination. SIGKILL recovery and hard watchdog termination
are tested. A fully OS-paused worker or helper also pauses its watchdog: an operator must
resume or terminate it. The watchdog requires its process/thread to be scheduled;
this is not a real-time or remotely guaranteed provider-cancellation mechanism. Recovery deliberately favors duplicate prevention over
availability while a live owner remains locked, Docker is unavailable, or the DB
is unavailable.

This is **not exactly-once distributed execution**. A crash after a provider call,
agent work, or artifact write but before DB commit can cause another attempt and
additional provider cost. The original repository is not modified: every attempt
uses a separately staged workspace. Retry does not replay an in-place patch into
an old workspace. Repositories are staged at attempt start, not snapshotted at
submission; keep approved sources immutable if retries must see identical input.

All workers **must run on one host, against the same Docker daemon, database and
shared artifact/lock filesystem**. Different local lock directories, NFS/object
storage mounts without equivalent locking, or workers on different hosts are not
supported. PostgreSQL row fencing alone cannot stop a partitioned worker's external
side effects. A multi-host version needs a separately designed execution-fencing
and cancellation boundary; do not remove the filesystem lock and keep the same claim.

## Agent recovery remains separate

Service recovery handles submission, claims, lease loss, process death and run
attempts. Existing `RecoveryPolicy` handles provider/tool faults, budgets, and
ambiguous mutations inside an attempt. In particular, a potentially executed
`apply_patch` with a lost response is reconciled through workspace revision and
`git_diff`, rather than blindly replayed. The frozen 8-scenario agent reliability
matrix and its unsafe-state counters remain separate evidence from service tests.

`SUCCEEDED` follows the existing agent's configured-test result. It is not a
hidden-test, official SWE-bench, or proof-of-correctness judgment. Agent stop reasons
and negative outcomes are persisted without rewriting them as successes.

## API contract

The OpenAPI schemas are available at `/docs` and `/openapi.json`. Task endpoints
require `Authorization: Bearer <REPOPILOT_API_TOKEN>`. Health endpoints carry no
sensitive task data and are public within the local bind. This is one shared
principal, not multi-tenant authorization.

| Route | Behavior |
|---|---|
| `POST /v1/tasks` | 202 new task, 200 idempotent replay, 409 key conflict, 422 invalid input |
| `GET /v1/tasks/{uuid}` | Lifecycle, resolved model, latest run and attempt metadata; 404 unknown |
| `GET /v1/tasks/{uuid}/result` | Terminal redacted result; 409 while pending |
| `GET /v1/tasks/{uuid}/trace` | Safe per-attempt metadata and available existing trace summaries |
| `GET /healthz` | Process liveness, independent of database |
| `GET /readyz` | Database connectivity and migration readiness; 503 unavailable |

Example request (unknown fields are rejected):

```json
{"repository":"example","issue":"Describe the bug and expected behavior","profile":"default"}
```

`repository` is a relative directory under the approved workspace root. Absolute
paths, traversal and symlink escapes are rejected, and the worker revalidates before
staging. Existing staging restrictions, size limits and no-symlink rules still apply.
Do not paste credentials into issue text. Credential-shaped and known control
secrets are rejected without echoing the submitted values. Idempotency keys are not
logged and are hashed rather than stored verbatim.

API validation failures omit the default raw-input echo. DB and execution exceptions
use fixed error codes rather than raw exception text. Existing trace redaction is
reused; the worker additionally scopes literal model/API-secret redaction around
execution before trace/result writes, including secrets appearing in mapping keys. Credentials come from worker environment only;
provider keys and credential configuration are absent from task schemas and DB
records. Pattern redaction cannot recognize every unrelated secret a user might
put into repository files; keep approved source directories free of secrets.

Every HTTP response has `X-Request-ID` (valid supplied UUID or a generated UUID).
The initial ID is stored with the task. Structured JSON log messages correlate
`request_id`, `task_id`, `run_id`, `trace_id` and `worker_id`; replay logs also identify
the original request. The trace ID is the existing deterministic ID derived from
the run UUID. `trajectory.jsonl` and reconciled `run.json` stay under
`<artifact root>/<run UUID>/`. Full raw file serving is deliberately absent: the API
returns safe metadata/summary projections and relative artifact references. Operators
can use the existing `repopilot trace-summary` command on the full local artifact.
This is correlated structured logging, not OpenTelemetry or distributed tracing.

## Local startup

Requirements: Docker with Compose, Python 3, and enough disk for images. From the
checkout:

```bash
./scripts/service-dev.sh
python3 scripts/service-smoke.py
```

The first command writes a private, git-ignored `.service.env`, creates an example
pytest repository and a separate artifacts directory under
`~/.local/share/repopilot-service`, builds the existing sandbox and trusted control
image, migrates PostgreSQL, and starts API + two workers. It defaults to explicitly
scripted execution: the real agent controller collects tests/diff, with no model
API calls and no attempt to generate a fix. This is a deployment smoke, not a coding
benchmark. The script preserves existing local configuration on subsequent runs.

The API binds only to `127.0.0.1:8000`. For manual requests, load your generated local
environment and submit:

```bash
set -a
. ./.service.env
set +a
curl -H "Authorization: Bearer $REPOPILOT_API_TOKEN" \
  -H 'Idempotency-Key: example-1' -H 'Content-Type: application/json' \
  -d '{"repository":"example","issue":"Collect tests and diff"}' \
  http://127.0.0.1:8000/v1/tasks
```

For real tasks, set `REPOPILOT_SCRIPTED=0`, `REPOPILOT_PROVIDER=openai` or `deepseek`,
and an explicit `REPOPILOT_MODEL` in `.service.env`. Export the corresponding
`OPENAI_API_KEY` or `DEEPSEEK_API_KEY` in the shell that launches Compose, then run:

```bash
docker compose --env-file .service.env up -d --scale worker=2
```

Model calls may incur provider charges. The smoke scripts refuse to run unless the
configuration and live API explicitly say scripted mode. Scripted workers reject
queued provider-backed tasks before model construction; changing execution mode does
not authorize real model calls in a scripted worker. Submit repositories beneath the approved
root; updating the root requires updating both API and workers. No `.env` is copied
into either Docker build context.

Inspect or stop the stack:

```bash
docker compose --env-file .service.env ps
docker compose --env-file .service.env logs -f api worker
docker compose --env-file .service.env down
```

`down` keeps the named PostgreSQL volume and host artifacts. `down -v` deletes DB
state and must only be used when that is intended. No automatic artifact retention,
backup policy or garbage collector is supplied; monitor disk and archive completed
artifacts administratively. Do not delete `.locks` while workers run.

## Docker trust boundary and paths

The worker is trusted control-plane code, running with Docker-daemon access. Its
host Docker socket is effectively host-administrator capability. The API has no
Docker socket or model credentials. PostgreSQL is internal to the Compose network.
Agent child containers retain network `none`, UID/GID 10001, read-only root,
capability drops, no-new-privileges, CPU/memory/PID limits and only their staged
workspace mount. Neither the worker socket, sibling artifacts, original source,
nor worker credentials enter the child sandbox. Live inspection tests verify this.

The artifacts directory is a **shared bind mount at the same absolute path inside
the worker and on the Docker host**. Docker resolves child bind sources on the daemon
host, not relative to the worker's container filesystem. A generic named artifact
volume at `/artifacts` would not make `/artifacts/...` a valid host bind source. This
is why PostgreSQL uses a named volume and artifacts use a deliberately shared host
bind. Docker Desktop must be allowed to share that host directory. Workers must all
use the exact same mount/inodes; remote Docker daemons are unsupported. A configurable
`REPOPILOT_DOCKER_SOCKET` can select the local host socket bind.

The only sandbox extensions are an optional operator-owned deterministic container
name and prebuilt-image mode. CLI/evaluation defaults still use random names and
build their image as before. The service fails if the prebuilt image is missing;
Compose builds it separately before workers start. Docker remains an isolation
boundary distinct from orchestration, and is not a VM-grade security guarantee.

## Host development and tests

Keep PostgreSQL running in Docker (a disposable DB for tests):

```bash
uv sync --frozen --extra dev --extra service
docker run -d --name repopilot-test-db \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=repopilot_test \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
export REPOPILOT_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/repopilot_test
uv run --extra service alembic upgrade head
docker build -t repopilot-sandbox:0.1.0 .
export REPOPILOT_TEST_DATABASE_URL="$REPOPILOT_DATABASE_URL"
uv run --extra dev --extra service pytest
```

**The service test fixture truncates task/run tables in the explicitly supplied test
database. Never point it at a database with tasks you want to retain.** Without the
service extra or test DB URL, service tests skip with a reason. CI supplies both and
runs the full suite, including Docker/MCP and frozen reliability regressions. No
model credentials are required. CI also runs the Compose smoke and real restart
checks. It does not rerun paid model pilots or download external benchmark harnesses.

For a host API and workers, set the same root/artifact/token/provider settings as
above, but use a host-reachable PostgreSQL URL and the normal host Docker CLI:

```bash
uv run --extra service uvicorn repopilot.service.api:app_factory --factory \
  --host 127.0.0.1 --port 8000 --no-access-log
# Separate terminals, with the same environment:
uv run --extra service repopilot-worker
uv run --extra service repopilot-worker
```

`REPOPILOT_LEASE_SECONDS`, `REPOPILOT_MAX_ATTEMPTS`, and
`REPOPILOT_HARD_TIMEOUT_SECONDS` configure finite recovery bounds. Use the same
settings across workers. Changing the hard timeout requires a matching Compose stop
grace period. Host Python 3.12 and 3.13 are exercised locally/through the container;
POSIX flock is required, so native Windows workers are not supported.

Run the narrowly scoped service tests and actual restart verification:

```bash
uv run --extra dev --extra service pytest tests/service
python3 scripts/service-restart-check.py
python3 scripts/service-crash-check.py
```

The restart check stops workers, submits a queued task, restarts API/PostgreSQL,
checks durability and idempotency, resumes workers, then restarts again and compares
the terminal result. It operates on the scripted local Compose stack. The crash check SIGKILLs the
worker owning a fixture task, verifies an ABANDONED first run and successful second
run, and verifies removal of the old child container. It is disruptive and must only
be used on the local scripted test stack.

## Reliability and concurrency matrix

| Boundary | Deterministic test/evidence |
|---|---|
| Submission / status / result | API lifecycle, pending 409, terminal retrieval, independent app instances |
| Idempotency | Same/different payload; four OS processes race one key; concurrent conflicting requests |
| Claim race | Four OS processes released by a barrier; exactly one persisted active attempt |
| Independent concurrency | Two workers rendezvous inside execution while HTTP serves another submission |
| Expired but live owner | Execution lock blocks replacement; late heartbeat and completion rejected |
| Owner process death | Kill real claim-owning process, expire DB lease, complete attempt two |
| Hard deadline | Stalled claim/execution exits with watchdog code 70; surviving helper enforces its own timeout |
| DB/heartbeat failure | Liveness remains 200, readiness/submission 503; failed heartbeat cannot commit success |
| Docker orphan / delayed requests | Real worker SIGKILL during Docker exec; helper lock and timeout survival; delayed patch and cleanup cannot target a replacement container |
| Bounded retries | Exhausted attempt never calls agent; cleanup outage remains recoverable |
| State guards | Invalid task/run transitions and duplicate RUNNING run rejected by PostgreSQL |
| Safe metadata | Correlation IDs, validation non-echo, invalid PostgreSQL text rejected with 422, secret redaction of values and mapping keys |
| Real deployment persistence | Queued task, idempotency and terminal result survive actual API/DB restarts |
| Existing runtime | Full core, CLI, local MCP, sandbox, deterministic benchmark and 8-case recovery regressions |

The executable tests are the acceptance evidence. See [SERVICE_AUDIT.md](SERVICE_AUDIT.md)
for audit findings and deployment checks, and [SERVICE_STRESS.md](SERVICE_STRESS.md)
for subsequent load/fault experiments and latest regression counts;
[SERVICE_ACCEPTANCE.md](SERVICE_ACCEPTANCE.md) retains the original pre-audit result. These are controlled
correctness tests, not load tests or a production reliability percentage.

## Trade-offs and limitations

- PostgreSQL provides durable state and transactional claiming with fewer operational
  components for the current bounded workload. A dedicated broker can be introduced
  behind the dispatcher if measured throughput warrants it; adding one now would
  not solve external-execution fencing.
- Asynchronous submission avoids binding long coding runs to HTTP lifetimes. One
  synchronous execution per worker keeps the existing runtime intact; more processes
  provide parallelism across tasks.
- Task/run separation preserves the user request and the history of failed attempts.
  Leases record process ownership durably; an in-memory mutex cannot represent death
  or survive restart. Shared execution locks add the fail-closed partition behavior
  needed by this single-host design.
- PostgreSQL persists metadata; shared files hold full artifacts. Disk quotas,
  retention, admission limits, multi-tenant authentication, TLS termination, backups,
  and operational alerting are not implemented. Keep the development API local.
- A fully paused owner, unavailable Docker daemon, or DB outage reduces availability.
  Retries can repeat provider calls and incur costs. In-flight provider requests may
  finish remotely even after the worker's hard process deadline.
- Substantially higher scale may warrant a dedicated queue, worker scaling,
  object storage, or different deployment. The local stress experiment does not
  demonstrate that all of these changes are required. Multi-host execution fencing and artifact ownership must be solved
  first. None of these prospective components is claimed or implemented here.
- Existing retrieval negative results, strict compatibility failures, stopped
  qualification gates, and the narrowly scoped 3/5 external pilot remain unchanged.
