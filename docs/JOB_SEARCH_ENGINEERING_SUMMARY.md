# Job-search engineering evidence summary

Frozen milestone: 2026-09-12. This is an evidence index for CV preparation and
technical discussion, not a CV or a production-readiness assessment.
[Closeout](JOB_SEARCH_CLOSEOUT.md) records final verification and exceptions.

## Demonstrated capabilities

| Capability | Implemented mechanism | Exact supporting evidence |
|---|---|---|
| Persistent asynchronous agent execution | FastAPI v1, separate PostgreSQL tasks/runs, Alembic migrations and worker processes calling the existing AgentLoop | 244 full regression tests passed; real Compose smoke and four API/DB restart assertions passed. [Service contract](SERVICE.md), [closeout](JOB_SEARCH_CLOSEOUT.md) |
| Concurrent ownership and idempotency | Hashed unique submission keys, transactional SKIP LOCKED claims, one active run per task, leases, stale-completion checks and shared POSIX execution locks | Final 12 scaling cases: 9,984 tasks / 19,968 submissions, zero failures or duplicate executions. Real-process races and paused/live-owner tests in [service tests](../tests/service/test_service.py) |
| Crash recovery and draining | Signal-driven stop of future claims, bounded watchdog, inherited execution fence, immutable Docker container IDs and fail-closed orphan cleanup | 12/12 stress interruption cases passed with zero simultaneous duplicates; Compose SIGKILL/orphan removal/second attempt passed. [Depth report](SERVICE_DEPTH.md), [audit tests](../tests/service/test_audit.py) |
| Measured PostgreSQL engineering | One pooled connection per worker, separate queued/recovery partial indexes, serialized admission function with fresh snapshots | 64-worker one-second case: peak connections 100 → 73; server limit rejections 63 → 0. Same-population queued-tail probe: old 11.132 ms / 60,102 hits vs queued 0.374 ms / 218 hits plus recovery 0.018 ms / 3 hits. Individual plans, not percentile claims. [Raw evidence](SERVICE_DEPTH_SCALING.json) |
| Bounded API and operational visibility | Shared bearer authentication, structured errors/request IDs, 128 KiB body bound, configurable active-task admission, authenticated Prometheus text and correlated JSON logs | Eight simultaneous clients against capacity two admit exactly two; full-capacity replay/conflict tests, metrics authentication/state/expiry tests and live metrics check. [Depth tests](../tests/service/test_depth.py), [acceptance](SERVICE_DEPTH.json) |
| Controlled tool execution and interoperability | Six typed tools, canonical registry, direct/local stdio MCP, staged networkless non-root Docker sandbox | Live Docker and one-task direct/MCP parity rerun in regression; 23 selected MCP tests passed in the [P0 checkpoint](checkpoints/P0_CHECKPOINT.json). Current 100-call fake-backend regression checks median below 10 ms; exact historical timings lack a retained raw artifact. |
| Agent recovery and evaluation discipline | Typed controller actions, bounded retry, revision/diff reconciliation, structured traces, failure taxonomy and frozen evaluation gates | Reliability 8/8: 6/6 recoverable and 2/2 expected stops; zero unsafe/state counters. Retrieval promotion failed despite semantic MRR 0.604 vs structural 0.573. [Checkpoint](checkpoints/P1_CHECKPOINT.json) |
| External benchmark integration with scoped outcomes | SWE-bench qualification, trusted tests, contamination/security gates, prediction export and official grading | Frozen Cohort 2: 5/5 qualified and gold sanity passed; one attempt per task, 3/5 officially resolved. Earlier 0/2 pilot and stopped 1/3 qualification remain preserved. Strict DeepSeek compatibility remains NOT COMPATIBLE at 9/12. [Behavioral report](checkpoints/DEEPSEEK_SWEBENCH_COHORT2_BEHAVIORAL.md) |

## Technologies actually used

Python; FastAPI, Pydantic and Uvicorn; PostgreSQL 16 with Psycopg connection pools,
SQL/PLpgSQL, Alembic and SQLAlchemy's migration connection; OS processes, threads,
POSIX flock and signals; Docker and Compose; pytest; Git and GitHub Actions workflow
configuration; uv/frozen dependencies; local stdio MCP; provider adapters including
OpenAI-compatible and DeepSeek calls; JSON/JSONL traces; Prometheus-compatible text
exposition; Python AST, lexical/local semantic retrieval and rank fusion; SWE-bench
integration and official grading artifacts. There is no deployed Prometheus server,
OpenTelemetry stack, broker, Kubernetes, cloud deployment or object-storage backend.

## Decisions and trade-offs

- PostgreSQL combines durable metadata and short coordination transactions. Agent
  execution holds no database transaction. It is sufficient for the measured local
  scope; a broker alone would not replace execution fencing.
- Database leases express ownership, but a paused or partitioned worker may still
  execute. Shared filesystem locks and surviving Docker supervisors protect the
  single-host boundary at an availability cost. Retrying may repeat provider calls.
- Admission defaults to 4096 active tasks and uses a serialized database function.
  Replicas must share configuration; trusted direct SQL bypasses admission policy.
  Capacity was deterministically tested at small limits, not a 4096-task soak.
- One connection per worker reduces connection pressure. API processes each have
  eight slots, so the connection budget must include workers, API replicas and
  operational headroom. More workers did not yield proportional throughput.
- Metrics summarize service behavior; traces preserve detailed agent evidence.
  Database gauges describe retained rows and repeat across API replicas; local API
  counters reset on restart. History aggregation/retention remain limitations.
- The artifact publisher abstracts metadata and references only. Local staging,
  bind mounts and execution fencing remain necessary. Running cancellation is not
  supported because local termination cannot prove remote provider work stopped.

## Results safe to cite

The full 100 ms workload comparison is preserved rather than selecting a peak:

| Workers | Before tasks/s, repetitions 1 / 2 | Final tasks/s, repetitions 1 / 2 |
|---:|---:|---:|
| 16 | 112.854 / 113.478 | 110.835 / 114.438 |
| 32 | 232.688 / 230.443 | 236.145 / 218.186 |
| 64 | 280.270 / 262.687 | 262.339 / 268.431 |

Each case used 1536 tasks, 32 concurrent submitters, two requests per key and a
100 ms scripted callable on one local machine. **No general throughput improvement
was established.** The one-second workload and complete p50/p95 measurements are in
[SERVICE_DEPTH.md](SERVICE_DEPTH.md). The 64-worker one-second final claim p95 was
88.275 / 130.017 ms, despite solving connection saturation. These are bounded local
experiments, not model-serving performance, a universal capacity limit or evidence
of statistically significant improvement.

Historical README figures for the exact MCP microbenchmark timings and controlled
DeepSeek 12-task run lack raw run artifacts in tracked checkpoints. They remain
labelled in the README for provenance and should not be used as independently
verified CV evidence. This does not affect the retained Cohort 2 official grading.

## Claims to avoid

Do not claim production-ready/production-scale deployment, exactly-once external
effects, multi-host coordination, general SWE-bench 60% success, a passed DeepSeek
strict compatibility gate, promoted semantic retrieval, VM-grade isolation, or a
hosted CI pass. The GitHub workflow push was rejected for missing `workflow` scope;
hosted execution remains externally unverified. Current authentication is one shared
trusted principal. No per-tenant quotas, TLS deployment, backups/restore drill,
long-duration soak, disk retention policy or robust running cancellation is claimed.

## Interview topics

Transaction boundaries and uniqueness races; isolation snapshots after lock waits;
lease versus execution fencing; crash recovery versus exactly-once side effects;
connection budgeting and EXPLAIN evidence; backpressure/idempotency interaction;
trusted controller versus untrusted repository code; metrics cardinality and gauge
semantics; ambiguous mutation reconciliation; controlled evaluation versus model
performance; negative results and frozen acceptance gates. Follow the
[interview map](INTERVIEW_ENGINEERING_MAP.md) into code and tests.
