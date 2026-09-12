# Service engineering-depth acceptance — 2026-09-12

This pass builds on the preserved service audit and stress evidence. It introduces
no broker, cloud infrastructure, programming language, or unrelated agent feature.
Final measurements and CI results are recorded below when verification completes.

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
process; scrape every replica separately. Metric labels use route templates,
finite outcomes and worker/task states. Worker claim timing covers the database
transaction through bookkeeping, excluding connection checkout and commit; the
stress harness continues to measure full claim latency independently. Duration
quantiles describe retained observations, not a sliding time window. Scraping uses
one read-only snapshot; large histories still incur aggregation cost and database
failure yields a safe 503 rather than fabricated zero values. No OpenTelemetry,
exhaustive duplicate-detection service, or detailed agent-trace replacement is claimed.
