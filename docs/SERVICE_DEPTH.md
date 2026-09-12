# Service engineering-depth acceptance — 2026-09-12

This pass builds on the preserved service audit and stress evidence. It introduces
no broker, cloud infrastructure, programming language, or unrelated agent feature.
Measurements below distinguish the historical baseline, the initial regression,
and the final implementation. All execution was scripted without paid model calls.

## Contract and implementation

- Revision `0002` adds a dedicated queued-task index, an expiry-ordered recovery
  index, and a worker registry with constrained states and nonnegative telemetry.
  Recovery candidates are queried through the expiry index and task primary key;
  queued work uses its own index. Expired recovery gets priority, then FIFO queued
  selection, with SKIP LOCKED and the existing filesystem execution fence. Heartbeat
  renewal is rechecked after locking the previous run. No execution transaction is
  held across agent work.
- Each worker uses one pooled connection; execution itself holds none. Heartbeat,
  claim and completion transactions serialize within that worker. The API retains
  eight connections. Aggregate connection planning is now approximately workers +
  eight API slots + operational headroom; this is not an unlimited-worker claim.
- Admission defaults to 4,096 QUEUED/RUNNING tasks, configured with
  `REPOPILOT_MAX_INFLIGHT`. All API processes must use the same operator configuration.
  Revision `0003` executes admission in one VOLATILE PL/pgSQL function call.
  A transaction-scoped PostgreSQL advisory lock serializes new admission; a fresh
  READ COMMITTED snapshot counts active tasks after obtaining the lock. Existing
  idempotency keys replay at capacity, conflicts remain 409, and rejected new tasks
  return 429 with Retry-After. Trusted direct SQL administration bypasses this
  application policy; lifecycle/uniqueness constraints remain database-enforced.
- POST/PUT/PATCH bodies are limited to 128 KiB before JSON parsing, including
  streamed bodies. Errors contain stable `code`, safe `detail` and `request_id`.
  OpenAPI declares the error schemas and HTTP bearer security. Existing bearer
  authentication remains a single trusted principal, not multi-tenant authorization.
  Liveness/readiness and API documentation remain public; task data and metrics
  require authentication. Request/task/run/trace IDs remain in logs and metadata.
- Authenticated `GET /metrics` serves Prometheus text format without a separate
  metrics server dependency. Finite-label counters cover HTTP requests/duration and
  created/deduplicated/conflicting/rejected admissions per API process. Retained DB
  gauges cover queue/task/run outcomes, stale leases, recovered attempts, first
  task wait and run duration distributions, worker availability/expiry/draining,
  and claim transaction timing. IDs and secrets are never metric labels.
- Metric worker liveness is lease-based, not proof a process is currently scheduled.
  A signal sets a local draining flag without database I/O; subsequent heartbeats
  report DRAINING. An in-flight claim checks draining before creating an attempt;
  a claim already committed drains as active work. Shutdown completes current work,
  records STOPPED when the DB is reachable, and closes the pool. Abrupt death leaves
  registry leases to expire and tasks follow the existing fenced recovery path.
- Running-task cancellation remains deliberately unsupported. Agent/provider calls
  are synchronous, and local termination cannot prove remote work stopped. Removing
  a sandbox alone cannot revoke delayed provider/tool activity. A robust cancellation
  feature needs a supervised execution boundary and explicit cancellation intent /
  completion arbitration; no misleading queued-only endpoint or new terminal state
  has been added. Worker draining is not task cancellation.
- `ArtifactPublisher` separates summary/reference publication from the execution
  adapter; `LocalArtifacts` is the only backend, validates UUID references and rejects
  symlink escapes. The API continues to serve PostgreSQL metadata and does not open
  client-supplied paths. Object storage could replace publication; it cannot replace
  local staging, Docker bind mounts, or filesystem execution fencing. Multi-host
  execution requires a separate redesign.

## Metrics interpretation

Database metrics are explicitly gauges over retained rows, not lifetime counters:
retention or operator deletion can decrease them. API counters reset with each API
process; scrape every replica separately. Database gauges repeat the same global
state on each API replica: scrape one or deduplicate them, never sum replicas.
Configured capacity and local API pool size/availability/waiters are also exposed. Metric labels use route templates,
finite outcomes and worker/task states. Worker claim timing covers the database
transaction through bookkeeping, excluding connection checkout and commit; the
stress harness continues to measure full claim latency independently. Duration
quantiles describe retained observations, not a sliding time window. Scraping uses
one read-only snapshot; large histories still incur aggregation cost and database
failure yields a safe 503 rather than fabricated zero values. No OpenTelemetry,
exhaustive duplicate-detection service, or detailed agent-trace replacement is claimed.

## Findings by severity and fixes

No new critical vulnerability or demonstrated overlapping execution was found in
this pass. These findings are additional to the preserved strict audit report.

| Severity | Finding | Resolution and evidence |
|---|---|---|
| High | Two connection slots per worker allowed the 64-worker heartbeat workload to reach PostgreSQL's 100-connection limit (63 server rejections in historical evidence). | One connection per worker; no connection spans execution. Both final workloads peak at 73 with 64 workers and zero server connection-limit rejections. |
| High | There was no explicit active-task admission limit; overload depended on database resource exhaustion. | Transactional global admission function, default 4096 active tasks; an eight-client barrier against capacity two admits exactly two. Replays/conflicts retain their semantics at capacity; completing a task releases capacity. |
| Medium | Dispatch inspected 10,000 healthy active leases to reach a queued task. | Separate partial queued and recovery indexes/queries preserve locking and expiry rechecks. On the same populated database, old queued-tail dispatch uses 60,102 buffer hits / 11.132 ms; queued dispatch uses 218 / 0.374 ms and recovery probe 3 / 0.018 ms. These are individual EXPLAIN ANALYZE observations, not latency percentiles. |
| Medium | A stop signal during claim selection could still admit another attempt without a drain check. | In-transaction drain check plus worker registry; deterministic boundary test and real SIGTERM experiment. A signal after the check can still produce an active claim, which is drained rather than abandoned. |
| Medium | API errors were inconsistent and task requests had no pre-JSON body bound. | Safe structured errors, correlation IDs, OpenAPI bearer/error contracts and 128 KiB streaming-aware limit. Existing authentication and redaction regressions retained. |
| Medium | First implementation held the new admission mutex across several client/database round trips, reducing burst throughput. | Preserved negative measurements; migration 0003 moves the critical section into one database function with fresh snapshots after waiting. Final throughput mostly recovers; no general throughput improvement is claimed. |
| Low | Service health/outcomes lacked a coherent metrics surface; artifact publication was embedded in execution. | Authenticated finite-label metrics and a small local artifact publisher, including symlink-escape rejection. No external backend or trace replacement. |

## Comparable scaling measurements

Same local machine (12 logical CPUs; Docker 12 CPUs / 8,217,165,824 bytes RAM),
PostgreSQL 16, 32 concurrent HTTP submitters, two requests per idempotency key,
two repetitions. Worker startup is excluded; elapsed time runs from the first
submission through final terminal observation and includes one-second polling.
This timing method is retained for comparability and limits short-run precision.
The 100 ms callable exercises real HTTP, Worker, Store, processes and PostgreSQL;
it does not exercise real model latency or Docker execution cost. Real Docker
execution is covered separately by regression and Compose checks.

### 1,536 tasks per case, 100 ms scripted execution

| Workers | Repeat | Before tasks/s | Initial admission tasks/s | Final tasks/s | Before claim p95 ms | Final claim p50 / p95 ms | Final peak DB connections |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 1 | 112.854 | 115.526 | 110.835 | 13.683 | 10.929 / 14.712 | 25 |
| 16 | 2 | 113.478 | 116.667 | 114.438 | 13.863 | 9.384 / 13.719 | 25 |
| 32 | 1 | 232.688 | 197.305 | 236.145 | 12.117 | 6.810 / 10.859 | 41 |
| 32 | 2 | 230.443 | 191.171 | 218.186 | 12.777 | 9.392 / 15.038 | 41 |
| 64 | 1 | 280.270 | 185.965 | 262.339 | 23.298 | 12.838 / 26.266 | 73 |
| 64 | 2 | 262.687 | 182.474 | 268.431 | 43.517 | 16.009 / 27.189 | 73 |

### 128 tasks per case, one-second scripted execution

| Workers | Repeat | Before tasks/s | Final tasks/s | Before peak connections | Final peak connections | Final claim p50 / p95 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 16 | 1 | 13.586 | 13.432 | 41 | 25 | 15.044 / 28.827 |
| 16 | 2 | 13.588 | 13.559 | 41 | 25 | 13.671 / 19.686 |
| 32 | 1 | 24.248 | 23.348 | 71 | 41 | 23.624 / 31.947 |
| 32 | 2 | 23.743 | 24.210 | 73 | 41 | 15.435 / 73.415 |
| 64 | 1 | 38.190 | 38.314 | 100 | 73 | 13.525 / 88.275 |
| 64 | 2 | 40.000 | 37.787 | 100 | 73 | 27.311 / 130.017 |

Connection-limit rejections fall from 63 to zero in the one-second workload.
Its final 64-worker claim p95 is **88.275 / 130.017 ms**: reducing connections does
not remove contention or make all latency better. Final 64-worker throughput is
also lower than the previous one-second measurements. The worker registry adds
writes and admission serializes new tasks; the experiment does not isolate their
individual costs. No significance claim is possible from two short repetitions.

### Final request, queue and completion timings

All values below are p50 / p95 milliseconds; full maxima and checkout/run timings
are retained in JSON. Across the 12 final scaling cases: **9,984 logical tasks,
19,968 HTTP submissions, zero failures, zero rejected submissions, zero sequential
or simultaneous duplicate executions**, with one attempt per task. Intentional
re-execution following interruption is reported separately.

| Duration | Workers | Repeat | Submit ms | Queue ms | End-to-end ms |
|---|---:|---:|---:|---:|---:|
| 100 ms | 16 | 1 | 50.207 / 67.828 | 6031.489 / 10952.667 | 6159.744 / 11078.825 |
| 100 ms | 16 | 2 | 46.875 / 58.733 | 5718.070 / 10531.588 | 5836.966 / 10662.272 |
| 100 ms | 32 | 1 | 55.297 / 68.096 | 2154.560 / 3542.850 | 2269.673 / 3652.875 |
| 100 ms | 32 | 2 | 50.375 / 70.235 | 2826.330 / 4344.776 | 2936.652 / 4467.502 |
| 100 ms | 64 | 1 | 51.795 / 100.360 | 1185.102 / 1314.358 | 1303.274 / 1435.390 |
| 100 ms | 64 | 2 | 51.212 / 99.786 | 1605.594 / 1733.658 | 1729.205 / 1859.722 |
| 1 s | 16 | 1 | 43.441 / 65.909 | 4108.602 / 8250.899 | 5147.562 / 9284.145 |
| 1 s | 16 | 2 | 44.315 / 63.399 | 4085.159 / 8165.429 | 5120.225 / 9210.940 |
| 1 s | 32 | 1 | 44.647 / 83.679 | 2053.027 / 4130.176 | 3112.386 / 5165.474 |
| 1 s | 32 | 2 | 43.530 / 87.340 | 1984.513 / 4003.531 | 3013.176 / 5031.384 |
| 1 s | 64 | 1 | 50.444 / 123.564 | 919.679 / 1855.125 | 1940.699 / 2875.323 |
| 1 s | 64 | 2 | 43.301 / 153.619 | 981.767 / 1966.399 | 2023.278 / 2998.110 |

### Interruption and contention checks

| Scenario | Final short recovery s | Final long recovery s | Attempts / final states |
|---|---:|---:|---|
| graceful | 0.693 | 0.702 | 1 / SUCCEEDED |
| kill | 3.363 | 3.364 | 2 / ABANDONED, SUCCEEDED |
| pause | 5.752 | 5.637 | 2 / ABANDONED, SUCCEEDED |
| db_restart | 12.249 | 12.165 | 2 / ABANDONED, SUCCEEDED |
| api_restart | 0.503 | 0.440 | 1 / SUCCEEDED |
| row_contention | 0.197 | 0.161 | 1 / SUCCEEDED |

All twelve interruption cases have zero simultaneous duplicates. SIGTERM completes
the active execution once; SIGKILL, paused ownership and DB restart recover through
an ABANDONED attempt. Queued work and idempotency survive API restart. The row-lock
case demonstrates another task can proceed. DB restart intentionally causes safe
connection/pool errors (the short run records two API PoolTimeouts); these are
preserved, not counted as steady-state successful requests or suppressed evidence.

Commands (each harness invocation also performs fault checks and plan probes):

```bash
uv run --frozen --extra service python scripts/service-stress.py --output docs/SERVICE_DEPTH_SCALING.json --cases 16:32,32:32,64:32 --tasks 1536 --repeats 2
uv run --frozen --extra service python scripts/service-stress.py --output docs/SERVICE_DEPTH_FINAL_LONG.json --cases 16:32,32:32,64:32 --tasks 128 --repeats 2 --duration 1
```

Evidence: [final short](SERVICE_DEPTH_SCALING.json),
[final long](SERVICE_DEPTH_FINAL_LONG.json),
[initial throughput regression](SERVICE_DEPTH_INITIAL_SCALING.json),
[initial longer workload](SERVICE_DEPTH_LONG.json). Each has an adjacent
`.postgres.log` preserving database failures and planned restarts. Historical
[SERVICE_STRESS.md](SERVICE_STRESS.md) and its raw files remain unchanged.

## Remaining limits and architecture decision

- This is a single-host execution design. POSIX locks, shared artifacts and Docker
  ownership are correctness dependencies. A broker or object store alone does not
  provide cross-host fencing, cancellation or exactly-once external side effects.
- At 32–64 workers the 100 ms workload already has diminishing throughput returns;
  64 workers is the tested ceiling, not a recommended deployment size. One API
  process plus 64 workers used 73 sampled connections. With max_connections=100,
  the planning budget is workers + 8 per API process + maintenance/reserved headroom;
  exhausting the remaining slots is not safe autoscaling. More API replicas reduce
  this worker budget. No universal broker threshold can be inferred from this run.
- If sustained required throughput exceeds the measured roughly 218–268 tasks/s
  range at 32–64 workers, or acceptable queue/claim latency cannot be met under the
  real job-duration mix, benchmark that requirement before increasing concurrency.
  Persistent admission-lock/DB contention after query and pool tuning would justify
  evaluating a broker; a multi-host requirement independently requires redesigned
  execution fencing. Neither need was demonstrated strongly enough to add one here.
- Admission is serialized and counts active rows. The configured 4096 cap bounds
  that workload, but capacity itself was tested at two and one, not a sustained
  4096-task saturation soak. Count cost, completion writes, idle polling, registry
  writes and retained-history metrics aggregation remain pressure points.
- Metrics are queried on scrape, not preaggregated. A large retained history can
  make scrapes expensive; registry/history retention and alert rules remain operator
  work. Recovery-first dispatch has no fairness SLA under a continuous stale backlog.
- API body size, active-task admission and DB-pool waiting are bounded; per-tenant
  quotas, HTTP connection/rate protection, TLS termination, backup/restore drills,
  multi-tenant authorization and disk quotas remain outside this local design.
- No running cancellation, object-storage backend, distributed worker scaling,
  paid-provider stress, long-duration soak, multi-host fault test, or production
  reliability/capacity claim is made.

## Demonstrated engineering scope

Real PostgreSQL transactions/constraints and migrations; authenticated asynchronous
HTTP contracts; concurrent idempotency and bounded admission; durable worker
claims, lease renewal and fenced recovery; graceful draining; scripted Docker
execution; finite-label service metrics with explicit semantics; a local artifact
publication boundary; repeatable stress measurements with negative evidence.
The verification below defines the extent of these claims.

## Final regression and acceptance

Full command (the URL points only at the disposable test database):

```bash
REPOPILOT_TEST_DATABASE_URL=postgresql://postgres:depth-only@127.0.0.1:55432/depth uv run --frozen --extra dev --extra service pytest --junitxml=/tmp/repopilot-depth-final.xml
```

**244 passed, 0 failed, 0 errors, 0 skipped, 1 warning in 95.99 seconds.**
The warning is Starlette's deprecation of the current httpx TestClient integration.
The service-only verification after migration 0003 passed 63 tests in 28.31 seconds.
The earlier 238-test audit/stress evidence remains intact. No paid model calls ran.

| Acceptance area | Final disposition |
|---|---|
| Core/CLI/MCP/sandbox and agent recovery | Full regression passed, including real Docker tests; existing frozen evidence preserved. |
| Service API, authentication, structured errors and correlation | Regression passed; rebuilt Compose smoke and authenticated/unauthenticated live metrics checks passed. |
| PostgreSQL persistence, lifecycle guards, separate task/run attempts | Real PostgreSQL regression passed; Compose migration upgraded retained data to revision 0003; all four live API/DB restart assertions passed. |
| Concurrent workers/idempotency/admission | Real-process and barrier tests passed; final 12 scaling cases have no duplicate execution or failed admission. |
| Claims, stale leases, crash recovery and draining | Final 12 stress interruption scenarios passed; Compose SIGKILL/orphan removal/second-attempt check passed. |
| Metrics and local artifact publication | Auth/labels/state/expiry/counters and symlink boundary regression passed. |
| Comparable scaling and transparent claims | Before/initial/final results above; negative measurements retained; no production-scale claim. |
| Hosted CI | **Externally unverified.** Workflow inspected and made manually dispatchable with read-only job permissions, frozen dependencies, real PostgreSQL/Docker tests and Compose restart/crash jobs. GitHub rejected the branch push because the available PAT lacks `workflow` scope; no remote branch or hosted run was created. SSH could not be used because no trusted GitHub host key was configured. |
| Cancellation | Explicitly unsupported, as permitted by this milestone's conditional requirement; no partial state machine is presented. |

The original milestone's hosted-CI execution criterion is therefore **not passed**.
All locally testable acceptance areas above passed within their stated single-host
boundaries; they are not proofs for arbitrary failures or untested deployments.
See [machine-readable acceptance](SERVICE_DEPTH.json) for source hashes and exact
Compose task IDs/results. The implementation and evidence are on the local branch
`codex/service-engineering-depth`; publication remains blocked by workflow-token
permissions. No credentials are included in the report or repository.
