# Local service stress and interruption verification — 2026-09-12

This experiment measures the current single-host PostgreSQL architecture. It adds
no broker, orchestration platform, cloud dependency, provider call, or agent feature.
The original acceptance, audit, evaluation and negative-result evidence remains intact.

## Reproduction and measurement contract

Use a local Docker daemon and the frozen service environment:

```sh
uv sync --frozen --extra dev --extra service
uv run --frozen --extra service python scripts/service-stress.py --output /tmp/service-stress.json
```

The harness creates and removes a randomly named PostgreSQL 16 container with an
OS-selected loopback port and a disposable database. It never connects to the demo
Compose database. API and workers are separate spawned OS processes; submissions
use actual HTTP, production FastAPI/Store/Worker code, real transactions and leases.
The fixed scripted executor performs no model calls. The production signal-handler
behavior is installed on each worker. Temporary workspace/artifact roots are shared
by workers; an independent per-task flock witness detects simultaneous execution.
The production execution fence and the witness use different files.

Default matrix: 192 logical tasks per case; each submitted twice, with the same
idempotency key, through 8, 32 or 64 concurrent submitters. Worker counts are
1, 2, 4, 8, 16 and 32. Submission concurrency is held at 32 for the main worker
comparison. Each configuration is repeated twice. A process-ready barrier excludes
worker startup; the one-second idle polling interval remains part of measured service
behavior. Queues, database rows and worker processes are fresh per case, not warmed
from the preceding case. Duplicate submissions are a second concurrent batch; the
separate regression test observes four simultaneous unique-index waits for one key.

Throughput is logical tasks divided by wall time from the first submission until
all tasks are observed terminal. This includes the duplicate HTTP batch, idle polling
and up to 100 ms observation delay. Submission timings include HTTP/network/API work.
Queue latency is database `run.started_at - task.created_at`; run timing includes
lease validation/execution/completion, and end-to-end latency is
`task.completed_at - task.created_at`. Claim timing includes connection setup and
commit, not just SQL execution. Quantiles use nearest rank. Connection/waiter counts
are sampled every 100 ms over one reused monitoring connection; peaks are lower
bounds and include that observer. Timing and scheduling vary between runs; deterministic
means fixed scripted work, barriers, bounded waits and asserted outcomes, not identical
wall-clock results.

Completed final measurement JSON reports record environment, source hashes, individual case metrics, PostgreSQL
plans, constraints, indexes and server configuration. These are local burst experiments,
not steady-state capacity, availability percentages, a coding solve-rate benchmark,
or production-scale evidence. The Docker VM has 12 CPUs and 8,217,165,824 bytes RAM;
the host runs macOS/arm64, Python 3.13.7, and PostgreSQL 16.15 in Docker. The existing
two-worker demo stack was idle and remained running during measurements. An
additional disposable regression PostgreSQL container was idle during post-pool measurements.

## Findings and changes

- **P2, fixed — redundant post-admission database work.** New-task POST opened a
  second connection, started a read transaction and selected the task and runs after
  INSERT had already committed. Stress instrumentation measured connection setup as
  a significant part of dispatch overhead. POST now returns the committed creation
  snapshot from `INSERT RETURNING`, with an empty run list. A concurrent worker may
  already have advanced the task; GET retrieves the newer state. Replays still read
  current state. This removes one connection and two SELECTs per new task (50% of
  its admission connections; 25% across a create/replay pair). It also removes one
  avoidable failure point after admission. This is an operation-count reduction;
  the changing monitoring harness and local variance do not support a clean causal
  percentage speedup claim. A regression checks snapshot consistency when a real
  worker finishes before the POST response.
- **P2, fixed for the reproduced workload — sustained connection churn disrupted admission and recovery.**
  The 1,536-task diagnostic burst at 32 workers returned 2,316 HTTP 503s. Only
  756 tasks were admitted; all eventually succeeded, with 24 sequential retry
  executions and zero simultaneous duplicates. API and workers recorded connection
  closures while PostgreSQL remained running. The exact transport-level cause of
  the closures is unverified, but the existing client opened a fresh connection
  for every database operation. Bounded in-process Psycopg pools now allow up to
  eight API connections and two per worker, with a three-second acquisition wait
  and connection validation before checkout. Transaction contexts still commit or
  roll back before a connection returns to the pool; execution holds no database
  transaction. API lifespan and worker shutdown close the pools. The same extended
  burst is rerun after the fix; this is a client-library change, with no new
  infrastructure service. New regressions exercise rollback/idle state, dead-backend
  replacement and a real waiter behind a two-connection limit. The lost-commit-ACK
  test still performs a real commit before injecting the lost acknowledgement.
  Pool context behavior follows the [Psycopg documentation](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).
- **P2, remaining query-scaling limitation — active leases, not completed history.**
  The existing partial `task_dispatch` index skips 100,000 completed tasks efficiently.
  Adding an explicit active-status predicate produces the same plan and does not
  justify an index migration. With 10,000 synthetic non-expired active runs, the
  ordered join visits every active task/run before concluding that nothing is
  claimable. A newly queued task behind those rows incurs the same scan. The
  `expired_runs` index exists but is not used to drive this dispatch plan. Separating
  queued candidates from indexed expired-run candidates is a future query change
  if such an active population becomes realistic. It was not required at the tested
  worker counts, so no speculative schema/query rewrite was made.
- **P3, fixed — documentation exceeded evidence.** The service guide previously
  said substantially higher scale would require a list of new infrastructure.
  It now treats those as options requiring measurement. Graceful shutdown wording
  explicitly includes a claim already in flight when the signal arrives.

The unique idempotency constraint, `(task_id, attempt)` uniqueness, one-active-run
partial unique index, same-task latest-run foreign key, terminal timestamp checks,
and transition triggers remain in place. Their definitions are captured alongside
plans; the full regression tests exercise their rejection paths. No new state-machine, simultaneous-execution fence, or sandbox-boundary defect
was demonstrated. Connection failures did cause the explicitly recorded sequential
retries in the pre-pool burst.

## Interrupted experiments and harness corrections

Failed experiments have not been relabeled as successful:

- `SERVICE_STRESS_PILOT.json`: preliminary 96-task measurements. It included startup,
  varied worker and submitter counts together, and used separate witness paths for
  replacement workers. Its fault overlap numbers are not independent evidence.
- `SERVICE_STRESS_INTERRUPTED.json` and `.log`: seven completed cases, followed by
  a failed observer connection. The original harness removed the database before
  retaining its logs, so the original connection-loss cause remains unverified.
- `SERVICE_STRESS_OBSERVER_INTERRUPTED.json`, `.log` and `.postgres.log`: eight
  completed cases followed by another observer connection failure. Docker reported
  PostgreSQL still running, not OOM-killed, and its log showed no crash/restart.
  This is not evidence of a PostgreSQL crash. The observer had opened a new database
  connection on every completion poll; it now reuses one connection and records/retries
  observation failures without discarding workload outcomes.
- `SERVICE_STRESS_REPRO.json`: two repetitions of the originally interrupted
  16-worker/64-submitter configuration and six strengthened fault cases passed.
- `SERVICE_STRESS_MATRIX.json`: all 16 final scaling cases passed. Its overall
  invocation then failed in graceful-shutdown verification because the harness
  signalled an already-draining worker a second time during interpreter shutdown.
  The harness now joins an already-signalled worker without sending another SIGTERM.
  The original overall failure flag remains; independent final fault evidence is
  `SERVICE_STRESS_FAULTS.json`.

The concrete causes of the initial observer connection closures are still unknown;
connection churn is measured, but attributing the closures specifically to Docker
port forwarding, PostgreSQL, resource limits or the OS would exceed the evidence.

- `SERVICE_STRESS_EXTENDED_INTERRUPTED.json`: the first extended-burst attempt
  returned 2,336 HTTP 503s, then its old completion predicate waited for tasks that
  had never been admitted. The harness now measures admitted/unadmitted tasks and
  waits for admitted work to become terminal. Rejected requests are not silently
  retried or counted as accepted work.
- `SERVICE_STRESS_EXTENDED_DIAGNOSTIC.json`: a subsequent diagnostic invocation
  stopped on its old assertion that no sequential retry execution could occur
  during connection failures. This was too strong for the documented lease/retry
  contract. The final pre-pool reproduction captures these outcomes explicitly in
  `SERVICE_STRESS_PREPOOL.json`, including its failure flag, HTTP rejection count,
  sequential retries, and zero simultaneous overlap. The assertions against
  simultaneous duplicate execution remain strict.

## Exact post-fix measurements

All five completed post-pool measurement invocations passed their task/admission/overlap checks: **13,080 tasks, 26,160 HTTP submissions, zero rejected/unadmitted tasks, zero terminal failures, zero sequential re-executions and zero simultaneous duplicates**. This excludes all interrupted/pre-pool runs. The one-second workload nevertheless generated **63 PostgreSQL connection-limit rejections during background pool growth**; existing connections served the tasks successfully. These server warnings are a remaining capacity signal, not erased by the task pass.

All paired latency columns below are **p50/p95 in milliseconds**, calculated independently for each repetition. Full run/acquisition distributions and sampled counts remain in the linked JSON.

### Matrix — [SERVICE_STRESS_MATRIX_FINAL.json](SERVICE_STRESS_MATRIX_FINAL.json)

| Workers | Submitters | Tasks | Repeat | Tasks/s | HTTP ms | Queue ms | Claim ms | End-to-end ms | Peak DB connections |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 32 | 192 | 1 | 7.575 | 40.343/56.385 | 12302.150/23705.930 | 10.256/12.081 | 12429.079/23831.616 | 10 |
| 2 | 32 | 192 | 1 | 15.439 | 40.582/54.392 | 5889.187/11551.931 | 9.344/12.838 | 6021.011/11672.342 | 11 |
| 4 | 32 | 192 | 1 | 27.831 | 41.757/56.742 | 3395.268/6196.846 | 8.972/15.465 | 3518.350/6318.757 | 13 |
| 8 | 32 | 192 | 1 | 62.526 | 46.668/66.212 | 1251.513/2562.421 | 7.651/15.274 | 1374.544/2686.801 | 17 |
| 16 | 32 | 192 | 1 | 81.647 | 48.692/65.289 | 1168.747/1736.153 | 8.576/28.451 | 1283.560/1861.383 | 25 |
| 32 | 32 | 192 | 1 | 138.196 | 48.532/74.629 | 521.928/943.432 | 5.442/66.527 | 634.330/1055.532 | 41 |
| 16 | 8 | 192 | 1 | 90.897 | 10.719/17.580 | 1047.654/1632.948 | 6.709/9.574 | 1166.420/1752.207 | 25 |
| 16 | 64 | 192 | 1 | 72.188 | 70.629/108.000 | 1577.535/2265.707 | 10.191/13.815 | 1701.423/2391.135 | 25 |
| 1 | 32 | 192 | 2 | 7.737 | 43.393/54.165 | 12108.766/23270.335 | 9.035/11.578 | 12226.850/23397.020 | 10 |
| 2 | 32 | 192 | 2 | 15.572 | 41.740/56.252 | 5743.127/11387.433 | 8.288/12.993 | 5859.098/11507.579 | 11 |
| 4 | 32 | 192 | 2 | 30.998 | 44.692/76.625 | 2779.553/5486.229 | 8.492/11.987 | 2903.244/5607.596 | 13 |
| 8 | 32 | 192 | 2 | 52.960 | 46.555/65.199 | 1682.842/3067.755 | 8.077/14.646 | 1802.703/3197.064 | 17 |
| 16 | 32 | 192 | 2 | 95.539 | 47.220/61.143 | 921.901/1477.829 | 6.246/34.832 | 1035.562/1594.807 | 25 |
| 32 | 32 | 192 | 2 | 129.105 | 48.466/69.305 | 626.347/1017.319 | 8.326/48.081 | 742.676/1135.289 | 41 |
| 16 | 8 | 192 | 2 | 89.895 | 10.596/17.869 | 1046.125/1602.701 | 6.293/9.406 | 1160.734/1720.886 | 25 |
| 16 | 64 | 192 | 2 | 90.258 | 80.007/116.256 | 1060.551/1682.372 | 7.291/16.469 | 1180.189/1802.164 | 25 |

### Large — [SERVICE_STRESS_POSTPOOL.json](SERVICE_STRESS_POSTPOOL.json)

| Workers | Submitters | Tasks | Repeat | Tasks/s | HTTP ms | Queue ms | Claim ms | End-to-end ms | Peak DB connections |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 32 | 1536 | 1 | 112.854 | 46.895/63.070 | 6061.356/11188.012 | 10.641/13.683 | 6190.972/11322.989 | 25 |
| 32 | 32 | 1536 | 1 | 232.688 | 50.406/66.591 | 2704.152/4300.541 | 5.944/12.117 | 2814.486/4411.189 | 41 |
| 16 | 32 | 1536 | 2 | 113.478 | 46.258/61.419 | 6050.073/11144.142 | 10.048/13.863 | 6174.208/11264.805 | 25 |
| 32 | 32 | 1536 | 2 | 230.443 | 49.018/68.261 | 2855.350/4561.364 | 6.059/12.777 | 2968.184/4683.736 | 41 |

### 64 workers — [SERVICE_STRESS_64.json](SERVICE_STRESS_64.json)

| Workers | Submitters | Tasks | Repeat | Tasks/s | HTTP ms | Queue ms | Claim ms | End-to-end ms | Peak DB connections |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 64 | 32 | 1536 | 1 | 280.270 | 52.790/98.994 | 1815.496/2237.558 | 13.757/23.298 | 1935.736/2356.242 | 73 |
| 64 | 32 | 1536 | 2 | 262.687 | 53.369/93.093 | 745.755/1089.457 | 12.344/43.517 | 866.663/1213.564 | 73 |

### One second — [SERVICE_STRESS_LONG.json](SERVICE_STRESS_LONG.json)

| Workers | Submitters | Tasks | Repeat | Tasks/s | HTTP ms | Queue ms | Claim ms | End-to-end ms | Peak DB connections |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 32 | 128 | 1 | 13.586 | 44.272/67.885 | 4068.740/8134.609 | 9.300/32.419 | 5103.268/9152.157 | 41 |
| 32 | 32 | 128 | 1 | 24.248 | 42.978/77.763 | 2028.796/4063.513 | 14.877/61.558 | 3072.237/5087.089 | 71 |
| 64 | 32 | 128 | 1 | 38.190 | 54.230/136.891 | 986.301/1921.964 | 40.453/135.195 | 2030.770/2949.464 | 100 |
| 16 | 32 | 128 | 2 | 13.588 | 41.578/56.518 | 4114.107/8210.378 | 11.280/37.724 | 5150.089/9240.440 | 41 |
| 32 | 32 | 128 | 2 | 23.743 | 52.944/85.350 | 2030.814/4073.802 | 14.965/66.063 | 3067.122/5115.218 | 73 |
| 64 | 32 | 128 | 2 | 40.000 | 49.801/94.106 | 924.785/1890.326 | 18.501/85.160 | 1958.039/2915.870 | 100 |

### Real agent — [SERVICE_STRESS_AGENT.json](SERVICE_STRESS_AGENT.json)

| Workers | Submitters | Tasks | Repeat | Tasks/s | HTTP ms | Queue ms | Claim ms | End-to-end ms | Peak DB connections |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 | 8 | 1 | 0.998 | 15.315/21.497 | 3009.372/7006.536 | 4.158/15.599 | 3993.020/7983.293 | 8 |
| 2 | 8 | 8 | 1 | 1.879 | 15.384/21.215 | 1098.604/3210.182 | 6.463/17.484 | 2197.290/4224.875 | 13 |
| 4 | 8 | 8 | 1 | 2.959 | 13.809/17.960 | 10.599/1333.185 | 10.167/20.064 | 1332.732/2674.074 | 13 |

The one-second executor exercises heartbeat activity. The real-agent sample uses the production AgentExecutor, ScriptedModel, AgentLoop, trajectory writer and restricted Docker sandbox against a tiny passing pytest repository. It ran 24 actual agent tasks at 1/2/4 workers, with no model API calls. Its single repetition and eight tasks per case are plumbing measurements, not coding capability or broad repository throughput.

### Final interruption results

From the last full matrix invocation, with real OS processes and the pooled implementation. Timings include deliberately imposed holds/outages and replacement startup; they are not recovery SLAs.

| Scenario | Measured seconds | Attempts | Final run history | Simultaneous duplicates |
|---|---:|---:|---|---:|
| graceful | 0.712 | 1 | SUCCEEDED | 0 |
| kill | 3.380 | 2 | ABANDONED → SUCCEEDED | 0 |
| pause | 5.723 | 2 | ABANDONED → SUCCEEDED | 0 |
| db_restart | 12.243 | 2 | ABANDONED → SUCCEEDED | 0 |
| api_restart | 0.470 | 1 | SUCCEEDED | 0 |
| row_contention | 0.203 | 1 | SUCCEEDED | 0 |

Graceful shutdown completes the held task, exits cleanly, and leaves the next task QUEUED until a replacement starts. SIGKILL loses its attempt and recovers after expiry. SIGSTOP holds a live owner beyond lease expiry while two competitors poll; no replacement starts until the owner resumes and releases its execution lock. PostgreSQL is stopped with zero grace and restarted while a task is held; competing workers cannot overlap the owner. API restart kills/recreates the HTTP process while a task runs. Row contention holds the oldest task row in a separate real transaction and verifies another task executes first. Intentional crash/recovery attempts are expected sequential retries, not simultaneous duplicates.

### PostgreSQL query-plan probe

The final matrix report captures `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, index/constraint definitions, PostgreSQL version/settings, and database transaction/deadlock statistics. Synthetic history has valid relational/state constraints but does not represent 10,000 actual worker processes.

| Probe | SQL execution ms | Shared buffer hits |
|---|---:|---:|
| original | 0.037 | 1 |
| explicit_active_predicate | 0.015 | 1 |
| active_10000_no_expired | 9.210 | 50100 |
| active_10000_one_expired | 0.022 | 9 |
| active_10000_queued_tail | 11.374 | 60102 |

These are individual plan executions, not repeated percentile estimates. `task_dispatch` handles completed-history exclusion; `runs_pkey` supplies the join lookups. A single expired run placed near the front can be cheap, whereas no expired run or a queued tail behind 10,000 live runs requires scanning those active entries. No extra index or migration was justified for the observed worker counts.

## Scale boundary and remaining limitations

The measured post-fix knee is **32–64 workers for 100 ms jobs on this machine**:
32 workers achieved 230.443–232.688 tasks/s in the extended bursts; doubling to
64 achieved 262.687–280.270 tasks/s, while claim p95 rose from 12.117–12.777 ms
to 23.298–43.517 ms. This is an observed burst envelope, not an arrival-rate limit
that can safely be sustained indefinitely. Queue p95 grows with burst size; the
one-second polling interval also materially affects small bursts.

The more immediate operational ceiling is the database connection budget. With
one API process and two possible connections per worker, reserve at least
`8 + 2 × workers + administration/monitoring headroom` connections. At the default
100-connection database limit, approximately 40 workers already consume nearly
all that budget if ten connections are reserved; **32 is the conservative tested
count with useful headroom**. The 64-worker short-job run used 73 sampled connections
because its pools did not need both slots simultaneously. With one-second work and
heartbeats, the 64-worker run reached 100 connections and PostgreSQL rejected 63
background connection attempts. Tasks still passed, but this configuration has
no safe connection margin. Pool pressure is now logged as fixed
`database_pool_warning` events without raw driver exception text. The safe warning
filter was added after timing measurements and is covered by the final regression;
connection-pool behavior and limits were unchanged.

**No measured broker requirement was demonstrated.** Connection reuse solved the
reproduced admission/recovery disruption within the existing architecture. A broker
would not eliminate task/result writes, heartbeat sessions, Docker resource limits,
or shared-filesystem execution fencing. Before increasing worker counts, budget
connections explicitly and investigate API/DB/host utilization; evaluate tighter
per-worker pool sizing or database limits with another bounded run. Before a much
larger active-run population, optimize the queued/expired candidate query.

A dedicated broker becomes a reasonable next experiment when sustained target-load
measurements show that queue claiming/backlog management still prevents the required
latency after those changes, or when requirements introduce durable cross-host
message delivery that this single-host design cannot provide. This experiment
cannot establish a universal broker threshold in tasks/second. Cross-host scaling
also needs a different execution fence and artifact-ownership design; adding a
broker alone would not make it correct. No broker or other infrastructure was added.

These runs do not verify a multi-hour soak, production availability, provider-bound
throughput/cost, large-repository Docker capacity, multi-host behavior, exhaustive
network/daemon faults, disk exhaustion, or fairness under a permanent stream of
new tasks. The real-agent sample is deliberately tiny. The pool acquisition limit
is bounded, but there is no general task admission quota or retention policy.
PostgreSQL connection-budget warnings and the original unexplained transport
closures remain documented. The single-host/shared-filesystem trust boundary and
at-least-once retry limitations remain unchanged.

## Final regression and deployment results

- `REPOPILOT_TEST_DATABASE_URL=… uv run --frozen --extra dev --extra service pytest --junitxml=/tmp/repopilot-stress-final.xml`
  — **238 passed, 0 failed, 0 errors, 0 skipped; 1 upstream TestClient deprecation
  warning; 96.24 seconds** (JUnit suite time 96.234 seconds).
- Breakdown: 181 original core tests, 39 original service tests, 12 prior audit
  cases, and 6 new stress/pool regressions. Real Docker, local MCP, CLI/runtime,
  fixed reliability scenarios, security and frozen evaluation checks are included.
- A service-only intermediate run passed 56 tests before the final warning-redaction
  test was added. An earlier invocation failed its 53 fixture setups before host
  database connectivity had been verified; no test bodies ran in that invocation.
- Rebuilt Compose API/workers with the final source and frozen dependencies. Scripted
  smoke passed; four actual API/PostgreSQL persistence restart checks passed; actual
  worker-container SIGKILL, orphan cleanup and successful second attempt passed.
- `uv run --frozen --extra service repopilot --help` passed.
- Hosted GitHub Actions was **not executed** here. Its configured workflow includes
  the regression suite; local execution does not certify a hosted CI run. The
  performance harness is an explicit local command, not a hardware-independent CI
  timing gate. No paid model calls or paid/SWE-bench benchmark reruns were performed.

The original specification's locally testable service/core/CLI/MCP/security/restart
criteria pass these bounded checks. Hosted CI execution remains unverified. The
specification does not establish production capacity or exactly-once execution;
this report does not claim either. Earlier audit/evaluation evidence is preserved.

## Files changed in this verification phase

- `scripts/service-stress.py`: disposable local benchmark/fault harness and metrics.
- `src/repopilot/service/store.py`: bounded optional Psycopg pools, connection checking,
  lifecycle cleanup and safe pool-warning logging.
- `src/repopilot/service/api.py`: committed creation snapshot and API pool lifecycle.
- `src/repopilot/service/worker.py`: bounded worker pool and shutdown cleanup.
- `tests/service/test_stress_regressions.py`: six deterministic database/pool tests.
- `tests/service/test_audit.py`: adapt the existing real-commit/lost-ACK wrapper to
  connection context managers while preserving its failure injection and assertions.
- `pyproject.toml`, `uv.lock`: existing Psycopg driver's pool extra, pinned to
  `psycopg-pool==3.3.1`; no new infrastructure service.
- `README.md`, `docs/SERVICE.md`, this report and `SERVICE_STRESS*.json/.log`:
  measured results, negative runs, query plans, updated limitations and reproduction.

The four pre-existing user edits in the core agent loop, repository context,
sandbox runner and tool registry were preserved without additional changes.

Additional reproduction commands:

```sh
# Extended bursts and fault checks
uv run --frozen --extra service python scripts/service-stress.py --output /tmp/large.json --cases 16:32,32:32 --tasks 1536 --repeats 2
# 64-worker short-job boundary
uv run --frozen --extra service python scripts/service-stress.py --output /tmp/64.json --cases 64:32 --tasks 1536 --repeats 2 --skip-faults
# Heartbeat/connection-budget pressure; the 64-worker case reaches the default DB limit
uv run --frozen --extra service python scripts/service-stress.py --output /tmp/long.json --cases 16:32,32:32,64:32 --tasks 128 --duration 1 --repeats 2 --skip-faults
# Real restricted Docker + scripted AgentLoop plumbing sample
# Requires the existing repopilot-sandbox:0.1.0 image; build with docker build -t repopilot-sandbox:0.1.0 .
uv run --frozen --extra service python scripts/service-stress.py --output /tmp/agent.json --cases 1:8,2:8,4:8 --tasks 8 --repeats 1 --agent --skip-faults
# Faults/query plans only
uv run --frozen --extra service python scripts/service-stress.py --output /tmp/faults.json --cases ''
```
