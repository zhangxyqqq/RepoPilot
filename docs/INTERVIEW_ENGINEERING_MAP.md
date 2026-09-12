# Interview engineering map

Frozen milestone: 2026-09-12. These are implementation drill-down notes and evidence
locations, not memorized answers. [Evidence summary](JOB_SEARCH_ENGINEERING_SUMMARY.md)
and [closeout](JOB_SEARCH_CLOSEOUT.md) define the supported scope.

## 1. Why asynchronous execution?

Long agent tasks → short HTTP admission → persisted task → independent worker.
`POST /v1/tasks` returns a committed creation snapshot (202); polling exposes later
state. The worker calls `run_agent` directly and no transaction spans agent work.
Inspect [API](../src/repopilot/service/api.py), [executor](../src/repopilot/service/executor.py)
and `test_independent_tasks_execute_concurrently_outside_http` in
[service tests](../tests/service/test_service.py). Discuss why a successful admission
is different from a successful task and how the result endpoint uses pending 409.

## 2. Why PostgreSQL, and where are transaction boundaries?

Durable task/run metadata → short claims/completions → explicit migrations.
Inspect [Store](../src/repopilot/service/store.py) and migrations
[0001](../migrations/versions/0001_tasks.py),
[0002](../migrations/versions/0002_service_depth.py),
[0003](../migrations/versions/0003_admission.py).
Foreign keys bind latest runs to tasks; a partial unique index prevents two RUNNING
rows for one task; transitions and timestamps are constrained. Admission takes an
advisory transaction lock and then fresh READ COMMITTED snapshots within a VOLATILE
function. Discuss why a single stale-snapshot CTE would not be equivalent.
[Uncommitted idempotency and pool rollback tests](../tests/service/test_stress_regressions.py)
exercise real transaction waits. SQL administrators remain trusted.

## 3. How are duplicate submissions and executions different?

Submission key hash + canonical fingerprint → same task or conflict. No key means
an independent request. Execution uses SKIP LOCKED claims + database leases + shared
filesystem flock + stale-completion checks. Lease expiry is not proof of process
termination. Inspect `submit`, `claim`, `heartbeat`, `finish` in
[Store](../src/repopilot/service/store.py),
[process fence](../src/repopilot/sandbox/process.py) and
[separate-process race/lease tests](../tests/service/test_service.py).
Discuss at-least-once attempts, repeated provider calls and why no exactly-once
external-side-effect guarantee follows from an idempotent POST.

## 4. What happens when a worker dies or is paused?

Missing renewal → expired ownership → execution fence still checked → orphan cleanup
→ another attempt or bounded exhaustion. Surviving Docker supervisors retain flock
until completion/timeout. Immutable container IDs keep delayed commands from hitting
a replacement with the same name. Inspect [worker](../src/repopilot/service/worker.py),
[cleanup](../src/repopilot/service/executor.py), [Docker adapter](../src/repopilot/sandbox/docker.py),
[audit tests](../tests/service/test_audit.py) and
[real Compose kill check](../scripts/service-crash-check.py).
Discuss safety versus availability when Docker is unreachable or an owner is paused.

## 5. How is concurrency tested rather than assumed?

Separate OS processes and barriers → competing claims/idempotency → simultaneous
independent execution → locked-row progress → scripted HTTP load.
[Service tests](../tests/service/test_service.py) include four-process races;
[audit tests](../tests/service/test_audit.py) rendezvous inside independent executions.
[Stress harness](../scripts/service-stress.py) measures submissions, queue/claim/run
latency, attempts and overlap. [Final evidence](SERVICE_DEPTH.md) records 9984 tasks,
19968 submissions and zero duplicate executions in scaling cases. Fault retries are
counted separately. Distinguish deterministic coordination from timing-based load
observations and an injected callable from actual Docker/AgentLoop integration.

## 6. What are the measured scaling limits?

Trace the initial saturation → pool budget → indexed queued/recovery queries →
admission round-trip regression → one-call database admission.
Read [before/initial/final measurements](SERVICE_DEPTH.md) and
[raw plans/timings](SERVICE_DEPTH_SCALING.json). Worker pool size is one; API pool
size is eight. The 64-worker one-second connection peak fell 100 → 73 with 63 → 0
server limit rejections. General throughput did not improve; two repetitions do not
establish significance. Discuss 32–64-worker diminishing returns for this workload,
not a universal worker ceiling, and retained-history scrape/admission costs.

## 7. Why no broker or orchestration layer?

Current bounded workload → measured PostgreSQL behavior → operational complexity
justified only by a demonstrated requirement. A broker would not solve shared
filesystem fencing, Docker ownership, remote side effects or cancellation.
[Architecture decision](SERVICE_DEPTH.md#remaining-limits-and-architecture-decision)
explains when sustained DB contention, unmet latency/throughput requirements or
multi-host execution would require new evidence and redesigned boundaries. These
are explicit non-goals for the frozen milestone, not implemented capabilities.

## 8. How does overload interact with idempotency?

Configured active-task cap → serialized admission → 429 + Retry-After for new tasks;
matching replay still 200 and conflict still 409. Completion releases capacity.
Inspect [admission function](../migrations/versions/0003_admission.py),
[body bound](../src/repopilot/service/bounds.py) and
[eight-client capacity-two test](../tests/service/test_depth.py).
Discuss why a task-count bound differs from HTTP rate limiting, a per-tenant quota
or a connection budget. Replicas must use the same configured cap.

## 9. What does graceful shutdown guarantee?

SIGTERM sets an in-memory drain flag → no future claims → a claim past the drain
check finishes as active work → STOPPED telemetry and pool close when possible.
A hard process watchdog and inherited helper timeouts bound stuck work. API restart
leaves worker execution independent; PostgreSQL restart can interrupt leases.
Inspect [worker](../src/repopilot/service/worker.py),
[drain tests](../tests/service/test_depth.py),
[restart script](../scripts/service-restart-check.py) and
[interruption results](SERVICE_DEPTH.md#interruption-and-contention-checks).
Running cancellation is absent; draining is not proof that remote work can be revoked.

## 10. Where is the security boundary?

Trusted host/API/worker configuration → staged repository → restricted child sandbox.
Workers have Docker authority; agents never receive the socket, arbitrary shell,
model credentials or controller test-command selection. Inspect
[Compose](../compose.yaml), [staging](../src/repopilot/sandbox/staging.py),
[registry](../src/repopilot/tools/registry.py),
[live sandbox tests](../tests/integration/test_docker_sandbox.py) and
[API schemas](../src/repopilot/service/schemas.py).
Bearer authentication is a single trusted principal, not tenant isolation. Docker
is defense in depth, not VM-grade protection against arbitrary hostile code.

## 11. How does agent-level recovery work?

Provider/tool failure → classify operation and execution certainty → bounded safe
retry or stop. Ambiguous mutation → workspace revision + git_diff reconciliation,
not blind replay. Inspect [AgentLoop](../src/repopilot/agent/loop.py),
[RecoveryPolicy](../src/repopilot/agent/recovery.py),
[unit tests](../tests/unit/test_recovery_policy.py) and
[reliability matrix test](../tests/regression/test_reliability_evaluation.py).
Frozen evidence: 8/8 expected outcomes, 6/6 recoverable and 2/2 expected stops with
zero unsafe-state counters. Keep this separate from service attempt recovery.

## 12. What can operators observe, and where are artifacts?

Service metrics → coarse finite-label operational state; logs → correlated request,
task, run and trace IDs; JSONL → detailed model/tool/controller evidence.
Inspect [metrics](../src/repopilot/service/metrics.py),
[logging](../src/repopilot/service/logging.py),
[trace schema](../src/repopilot/trajectory/schema.py) and
[local artifact publisher](../src/repopilot/service/artifacts.py).
Discuss retained gauges versus restartable counters, duplicate gauges across API
replicas, claim timing excluding checkout/commit, and scrape aggregation cost.
The publisher returns metadata/references; it does not implement object storage or
replace local staging and locks. [Boundary tests](../tests/service/test_depth.py)
reject artifact symlink escape.

## 13. How do you know the agent and evaluation work?

Controlled fixtures test infrastructure; local MCP parity tests transport contracts;
frozen compatibility gates test protocol use; official SWE-bench grading tests the
submitted patch on a small qualified cohort. These answer different questions.
Inspect [MCP parity](../tests/integration/test_mcp_controlled_smoke.py),
[taxonomy](../src/repopilot/evaluation/taxonomy.py),
[retrieval checkpoint](checkpoints/P1_CHECKPOINT.json),
[strict compatibility](checkpoints/MODEL_COMPATIBILITY_DEEPSEEK.md) and
[Cohort 2 behavioral evidence](checkpoints/DEEPSEEK_SWEBENCH_COHORT2_BEHAVIORAL.md).
Semantic retrieval failed promotion; DeepSeek remains NOT COMPATIBLE at 9/12 under
the strict gate despite separate pilot admission; 3/5 official resolutions are not
a general 60% solve rate. No paid runs were repeated during closeout. Hosted CI
remains externally unverified despite local regression success.
