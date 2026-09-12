# Service milestone acceptance — 2026-09-12

> Historical pre-audit report. The 220-test result below is retained, but the audit
> found real crash-path, execution-mode, deadline, redaction and validation defects
> that this suite missed. Its blanket concurrency/completeness conclusions are
> superseded by [SERVICE_AUDIT.md](SERVICE_AUDIT.md), which records the fixes and new
> controlled evidence. Passing tests do not establish correctness for all failures.

The implemented service passed its local functional acceptance checks. The GitHub
Actions workflow is configured but has **not been executed on GitHub** in this task;
therefore this report does not mark every criterion as independently verified.
Machine-readable results are in [SERVICE_ACCEPTANCE.json](SERVICE_ACCEPTANCE.json).

## Measured verification

- Restored existing baseline: **181 passed**. Docker was initially stopped; starting
  the local daemon resolved the initial infrastructure failures without changing
  tests or benchmark expectations.
- Final complete suite: **220 passed, 0 failed, 0 errors, 0 skipped**, in **78.53 s**:
  **181 existing tests + 39 service tests**. PostgreSQL was real PostgreSQL 16 in a
  dedicated disposable Docker database; service races use separate OS processes and
  independent DB connections, not SQLite or a mocked claim lock.
- One warning: the installed Starlette TestClient deprecates its `httpx` path in favor
  of `httpx2`. It did not fail tests; no repository lint/type pipeline previously
  existed, so no unrelated lint/type gate was added.
- Compose built and ran API, PostgreSQL and **two workers**. A scripted-provider
  task passed through the existing `run_agent`, tests, Docker sandbox and trajectory
  lifecycle. The worker used a prebuilt image and a shared host artifact bind.
- **4/4 actual restart checks passed**: queued-task durability, idempotent replay,
  resumed worker execution, and completed-result durability across API/PostgreSQL
  restarts. These checks exercise persistent storage, not just recreated Python
  objects or connections.
- Existing `repopilot --help` exposes the unchanged seven CLI workflows. Core CLI,
  MCP stdio/parity, sandbox security, deterministic benchmark, recovery and historical
  evidence regressions all passed in the complete suite.
- New modules compile successfully. Whitespace checks pass for milestone changes.
  Two trailing-whitespace warnings remain in the user's pre-existing
  `repository_context.py` changes, which were preserved.

Final test command:

```bash
REPOPILOT_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/repopilot_test \
  uv run --frozen --extra dev --extra service pytest --junitxml=/tmp/repopilot-final-tests.xml
```

Deployment commands verified:

```bash
./scripts/service-dev.sh
python3 scripts/service-smoke.py
python3 scripts/service-restart-check.py
```

No paid-model calls, external SWE-bench reruns, hosted GitHub Actions run, load tests,
or multi-host tests were performed. Paid/historical benchmark reruns are outside
this deterministic milestone; remote CI requires the changes to be published to
GitHub. Multi-host operation is explicitly unsupported. No test in the final local
suite was skipped.

## Acceptance checklist

| Requested criterion | Result / evidence |
|---|---|
| Existing core tests | PASS — all 181 existing tests |
| CLI workflows | PASS — help and unchanged CLI regression tests |
| Existing MCP behavior | PASS — direct/MCP parity and actual stdio integration |
| Sandbox guarantees | PASS — existing security tests and live service orphan-container inspection |
| FastAPI service | PASS — API integration and live Compose smoke |
| Real PostgreSQL persistence | PASS — Alembic migrations and PostgreSQL-backed tests |
| Tasks survive restart | PASS — actual API/PostgreSQL restart checks |
| Execution outside HTTP lifetime | PASS — worker barrier holds execution while HTTP continues |
| Multiple workers | PASS — process races, concurrent tasks, two Compose workers |
| No simultaneous execution of one task | PASS within supported shared-host/daemon/filesystem boundary — PostgreSQL claims plus interprocess flock and orphan cleanup |
| Concurrent idempotent submissions | PASS — four-process race, unique key, fingerprint conflict tests |
| Worker death / stale lease recovery | PASS — real process kill, stale lease, orphan cleanup, watchdog and bounded attempt tests |
| Separate tasks and runs | PASS — schema, attempt history, foreign keys and lifecycle tests |
| Existing agent recovery | PASS — frozen 8-scenario recovery regression remains intact |
| Correlated service logs | PASS — persisted request/run/trace metadata and structured log tests |
| Health and readiness | PASS — usable DB, unavailable DB, missing revision and process liveness |
| Docker Compose | PASS — build, migration, two workers, agent smoke and actual restarts |
| CI runs deterministic tests | CONFIGURED; hosted execution NOT VERIFIED — workflow runs full suite and separate Compose/restart job without model credentials |
| Accurate README | PASS — service-first diagram, separate agent architecture, explicit limits and retained evidence |
| No unsupported claims | PASS — correctness evidence is scoped; no exactly-once, availability or scale claim |

**19 criteria verified locally; 1 configured and awaiting hosted CI execution.**
All requested functionality is implemented within the documented single-host
boundary; no feature-invalidating TODO is left behind.

## Added files

- `src/repopilot/service/__init__.py`
- `src/repopilot/service/settings.py`
- `src/repopilot/service/schemas.py`
- `src/repopilot/service/logging.py`
- `src/repopilot/service/store.py`
- `src/repopilot/service/api.py`
- `src/repopilot/service/executor.py`
- `src/repopilot/service/worker.py`
- `alembic.ini`
- `migrations/env.py`
- `migrations/versions/0001_tasks.py`
- `Dockerfile.service`
- `compose.yaml`
- `scripts/service-dev.sh`
- `scripts/service-smoke.py`
- `scripts/service-restart-check.py`
- `tests/service/conftest.py`
- `tests/service/test_service.py`
- `.github/workflows/tests.yml`
- `docs/SERVICE.md`
- `docs/SERVICE_ACCEPTANCE.md`
- `docs/SERVICE_ACCEPTANCE.json`

## Changed files

- `README.md`: service framing and links; historical results retained.
- `pyproject.toml`, `uv.lock`: optional service dependencies and worker entry point.
- `.dockerignore`, `.gitignore`: exclude local configuration/secrets.
- `src/repopilot/config.py`, `src/repopilot/sandbox/docker.py`: optional trusted
  container identity and prebuilt-image mode; existing defaults/security unchanged.
- `src/repopilot/trajectory/schema.py`: optional scoped literal-secret redaction;
  default CLI/evaluation redaction behavior retained.

The four pre-existing user edits in `agent/loop.py`,
`sandbox/repository_context.py`, `sandbox/sandbox_runner.py`, and
`tools/registry.py` were not changed by this milestone. No benchmark, configuration
fixture, report or historical checkpoint was edited.

## Remaining limitations

Workers must share one host, Docker daemon and lock/artifact filesystem. Leases alone
are insufficient fencing for multi-host external execution. Retried attempts may
repeat provider costs; there is no exactly-once guarantee. A fully OS-paused worker
requires operator resumption/termination. Database or Docker outage postpones
recovery. The API has one shared bearer principal and binds locally; multi-tenancy,
TLS termination, admission control, retention, backups and operational alerting are
not implemented. Repository snapshots are staged at attempt start. Full details
and operational trade-offs are in [SERVICE.md](SERVICE.md).
