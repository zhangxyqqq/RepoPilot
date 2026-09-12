# Post-implementation service audit — 2026-09-12

This review inspected the working-tree implementation, including untracked service
files, SQL migrations, worker lifecycle, Docker calls, API schemas, tests, Compose,
CI and README changes. It did not rely on the earlier acceptance summary. No new
agent capabilities or unrelated service features were added.

**Six correctness findings were confirmed and fixed.** The earlier 220-test pass
was real, but its tests missed these cases. Its blanket concurrency/completeness
conclusion was too strong. The original report and JSON remain as historical
pre-audit evidence, with a supersession notice.

## Findings by severity

### P1 — Surviving or delayed Docker commands could affect a replacement attempt

The execution lock belonged only to the worker process. Killing a host worker did
not necessarily kill its Docker CLI subprocess; the latter could survive after the
lock was released. Furthermore, `exec`, sandbox close, and orphan cleanup addressed
a reusable task-specific container **name**. A command delayed before dispatch could
therefore resolve that name to a replacement container.

Two pre-fix reproductions failed: a replacement claim succeeded while a real Docker
exec remained blocked inside pytest, and a captured old `apply_patch` command
successfully applied its patch to a real replacement container with the reused name.
This was an observed state-isolation defect, not just a speculative race.

Fix: service Docker commands now run under a small supervisor that inherits the
same flock descriptor and owns the subprocess timeout independently of the worker.
The Docker CLI also inherits the descriptor. Lock release uses close, never explicit
unlock, so a surviving helper retains ownership. Tool calls and close target the
immutable ID returned by `docker run`; orphan cleanup resolves the name to full IDs
and removes those IDs. The lock FD stays in trusted control-plane processes and is
not passed into the agent container. Outside service execution, the process wrapper
uses the existing subprocess behavior.

Post-fix tests cover real worker SIGKILL during Docker exec, helper completion and
reclaim, helper timeout after worker death, delayed patch rejection, delayed cleanup
rejection, and actual Compose worker SIGKILL/recovery. When the worker container
itself stops, its in-container helpers stop too; separate child-sandbox cleanup is
still required. See `sandbox/process.py`, `sandbox/docker.py`, `service/executor.py`
and `service/worker.py`.

### P1 — Scripted workers could execute queued provider-backed tasks

`AgentExecutor` checked whether scripted tasks were permitted, but did not reject
the reverse mismatch. Switching a stack to scripted mode while provider-backed
tasks remained queued could still construct and call a real provider, potentially
incurring cost despite the scripted-mode expectation.

The pre-fix test reached the provider factory under `scripted=True`. It injected a
factory failure before any network call; no paid model call was made in the audit.
The adapter now rejects that mismatch before model construction. An already-running
provider request is not retroactively cancelled by changing configuration.

### P2 — Lease loss during cleanup did not prevent starting the agent

Initial cleanup could exceed the lease, but the worker entered the executor anyway;
it only fenced completion later. The pre-fix test expired the real PostgreSQL lease
inside cleanup and observed an executor call.

The worker now checks its observed lease-loss flag and renews/revalidates the lease
immediately after cleanup, before authorizing agent work. Expired ownership leaves
the attempt recoverable without starting a new provider/controller execution.
Flock remains the external-execution fence; a lease check alone is not a distributed
cancellation primitive.

### P2 — The worker watchdog did not cover claiming

The watchdog was started after `Store.claim()` returned. A stalled filesystem or
established database connection during claiming could therefore hold an execution
lock without the claimed hard timeout ever starting. Connect and server statement
timeouts are not a complete client-side wall-clock bound under all stalls.

A real worker process with an injected blocked claim outlived its configured deadline
before the fix. The watchdog now starts before claiming and is cancelled on all exit
paths. Both blocked-claim and blocked-execution process tests observe exit code 70.
This test injects the blocked boundary; it is not a claim that every network-partition
mode has been reproduced. An OS-paused process also pauses its watchdog.

### P2 — Literal secrets in dictionary keys reached traces

Scoped literal-secret redaction covered string values but left mapping keys intact.
The pre-fix recorder test wrote a synthetic opaque provider secret as a metadata key
and found it verbatim in `trajectory.jsonl`.

Mapping keys now pass through the same text redactor. The regression verifies that
neither secret-bearing values nor keys reach the trace. This is literal/pattern
redaction, not detection of every transformed or unrelated secret in source files.
No actual user credential was used or exposed in the reproductions.

### P2 — Invalid PostgreSQL text was reported as database unavailability

An issue containing a NUL character passed the request schema and failed during
PostgreSQL JSONB insertion, producing HTTP 503. This was a malformed client request,
not a database outage. The pre-fix API test observed 503 and no inserted row.

The schema now rejects NUL and non-UTF-8-encodable text before persistence, returning
422 without echoing input. Lone-surrogate input was already rejected by the existing
validation path; its passing test is retained alongside the new NUL regression.

## Test/evidence corrections

- The original four-process claim and idempotency races use real PostgreSQL and
  interprocess locking; they were not mocked-away races. Their limitation was that
  they did not cover surviving helpers or delayed requests across name reuse.
- Added a two-process execution rendezvous for independent tasks, supplementing the
  original thread-based HTTP/concurrency test.
- Added a lost-commit-acknowledgement injection **after an actual PostgreSQL commit**.
  The task remained terminal, a later worker did not claim it, and execution count
  stayed one. No transaction bug was found in this path.
- Heartbeat tests now distinguish periodic renewal from the new pre-execution check.
  The failure test releases the executor only after the worker has observed the
  heartbeat failure, avoiding a test-only race with its exception handler.
- Process-race tests clean up child processes on assertion/timeout failures.
- Added a disruptive, scripted-only Compose crash check, including restoration of
  the deliberately stopped worker. CI is configured to run it.
- README points to this audit. `SERVICE.md` now describes helper ownership, immutable
  Docker IDs, pre-execution lease checking, and the watchdog's scheduling limitations.
  The old acceptance report is explicitly historical rather than silently rewritten.

## Final results

**232 passed, 0 failed, 0 errors, 0 skipped**, in **91.30 seconds**:

- 181 existing core/CLI/MCP/sandbox/evaluation/evidence tests;
- 39 original service tests, including synchronization improvements;
- 12 additional audit cases.

One upstream warning remains: Starlette TestClient deprecates its `httpx` path in
favor of `httpx2`. There was no test failure associated with it. Code compilation
and whitespace checks for milestone changes passed. Pre-existing user whitespace
warnings and the four user-edited core files remain untouched.

```bash
REPOPILOT_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/repopilot_test \
  uv run --frozen --extra dev --extra service pytest \
  --junitxml=/tmp/repopilot-audit-final.xml
```

The rebuilt Compose stack passed its scripted agent smoke, **4/4 actual API/database
restart checks**, and the **worker SIGKILL / orphan-removal / successful-second-attempt
check**. It was left with the API, PostgreSQL and both workers running. The separate
throwaway audit database was removed after tests.

The pre-fix audit produced seven failing cases across the six findings, plus one
passing lone-surrogate validation case. After fixes, all twelve added audit cases
pass. The full suite was also run at an intermediate audit checkpoint: 232 passed
in 91.60 seconds. Machine-readable current results are in
[SERVICE_AUDIT.json](SERVICE_AUDIT.json); the earlier acceptance JSON is retained.

## Acceptance status and limits

The original functional acceptance criteria are covered locally by the full suite
and live deployment checks, within the documented shared-host boundary. **Hosted
GitHub Actions execution remains unverified**: the workflow exists and its local
commands passed, but it has not been run on GitHub from these changes. No production,
load, multi-host, exhaustive partition/daemon-failure, or paid-provider test was run.
Historical SWE-bench/paid-model experiments were not rerun or altered.

There are no remaining confirmed findings from this audit left unfixed. That is a
bounded review conclusion, not proof against all possible failures:

- All workers require the same host, Docker daemon, PostgreSQL database, and shared
  lock/artifact filesystem. Different lock namespaces and multi-host execution are
  unsupported. Trusted workers retain Docker-administrator capability.
- The API has one shared bearer principal and binds locally. Multi-tenant controls,
  TLS termination, admission limits, retention, backups and operational alerting
  remain outside this milestone.
- Leases, watchdogs and supervised helpers do not provide true exactly-once execution
  or remote provider cancellation. Interrupted attempts can repeat provider cost.
- Paused workers/helpers require operator intervention; unavailable Docker/PostgreSQL
  postpones recovery. The tests do not exhaust every daemon-side in-flight request
  or network-partition outcome; orphan inspection may still be needed operationally.
- Repositories are copied at attempt start, not snapshotted on submission. The
  Compose crash fixture deliberately updates its source between attempts to test
  plumbing; its success is not coding-agent capability evidence.
- Existing negative retrieval results, strict compatibility failures, stopped gates,
  and the narrowly scoped SWE-bench result remain unchanged.

## Files touched by this audit

Production fixes: `src/repopilot/sandbox/process.py` (new), `sandbox/docker.py`,
`service/executor.py`, `service/worker.py`, `service/schemas.py`,
`trajectory/schema.py`, plus a lock-lifetime comment in `service/store.py`.

Verification: `tests/service/test_audit.py` (new), `tests/service/test_service.py`,
`scripts/service-crash-check.py` (new), and `.github/workflows/tests.yml`.

Evidence/docs: `README.md`, `docs/SERVICE.md`, the supersession notice in
`docs/SERVICE_ACCEPTANCE.md`, and new `docs/SERVICE_AUDIT.md` / `SERVICE_AUDIT.json`.
No agent-loop/recovery algorithm, tool catalog, benchmark, config fixture, historical
checkpoint or original user edit was modified by this audit.
