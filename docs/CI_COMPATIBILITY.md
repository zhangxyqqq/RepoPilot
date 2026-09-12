# Hosted CI compatibility correction — 2026-09-12

This is a compatibility patch after the job-search freeze, not a service redesign.
The earlier closeout and measurement files remain historical evidence.

## Hosted failure and root cause

[Actions run 34700878013](https://github.com/zhangxyqqq/RepoPilot/actions/runs/34700878013)
executed commit `c63e9744fa072343d8e47686f8f387cf5728c915`. Its tests job reported
**33 failed, 211 passed, 1 warning in 77.48 seconds**. Repeated failures were
`repository initialization failed: fatal: not in a git directory`; other failures
were downstream missing barrier files, failed agent tasks or retry timeouts.

The staged repositories were initialized explicitly. Staging intentionally strips
the source `.git`, then `_init_repo` runs `git init`, configures identity and commits
a baseline. On native Linux, the directory keeps the host/controller owner while
the sandbox process runs as UID 10001. `git init` succeeds; the next `git config
user.name` refuses repository discovery because the working-tree owner differs.
Its misleading error is “not in a git directory”; `git status` reveals “detected
dubious ownership”. Making the directory writable does not satisfy Git's ownership
check. Neither checkout history nor a missing fixture `git init` caused this failure.
Host checkout safe-directory configuration is not forwarded into the sandbox.

A local measurement explains why the previous suite passed: the macOS staged
folder had host UID 501, but Docker Desktop exposed that bind mount to the sandbox
as UID 10001, matching the process. Native Linux Docker volumes retain ownership
and reproduce the failure locally without depending on a GitHub runner.

## Minimal correction and security boundary

Every controller Git invocation in `sandbox_runner.py` now includes the
command-scoped setting `-c safe.directory=<controller-selected workspace>`.
This covers initialization, identity, baseline commit, Git patch checking/application
and diff collection. It does not trust `*`, alter host/global configuration, forward
credentials, change container UID, change mounts or relax sandbox permissions.
The source `.git` is still excluded, and a new staged baseline is still required.

The new parametrized Docker integration test uses a Linux volume and directory
owners 1001 (host-runner case) and 0 (Compose-controller case). Only fixture setup
uses root. The actual initialization/patch/diff verification runs as UID 10001 with
network disabled, read-only root, dropped capabilities and no-new-privileges.
It starts without `.git`, verifies one baseline commit and a clean initial diff,
applies a Git-format patch, and verifies that another foreign-owned repository is
still rejected. It also checks that no global safe-directory entry was installed.

Both ownership cases **fail against the pre-fix runner with the exact hosted error**
(2 failed in 2.07 seconds) and **pass against the fixed runner** (2 passed in 2.68
seconds). No failing test was skipped, xfailed or weakened.

## Separate Compose investigation

The Compose job was inspected independently. Image builds, PostgreSQL health,
migration and API/worker startup succeeded. The smoke POST returned 202, its replay
returned 200, a worker claimed the task, then the attempt ended with the sanitized
`execution_error`; the smoke assertion failed because the task was FAILED. This was
not a Compose startup, migration, API authentication or readiness failure.

Compose controllers run as root and create staged workspaces owned by UID 0 on
Linux; the child sandbox still runs as UID 10001. The independently tested root-owned
case reproduces the same Git failure and passes with the fix. Hosted worker logs
intentionally omit the raw exception, so they alone cannot exclude an additional
execution issue. Rebuilt local Compose checks and the new native-ownership test
provide the verification below; a new hosted run remains the final environment check.

## Final verification

**246 passed, 0 failures, 0 errors, 0 skipped, 1 warning in 101.33 seconds.**
The warning is the existing Starlette/httpx TestClient deprecation. The two added
ownership cases are included in 246. No paid model calls were made.

```bash
REPOPILOT_TEST_DATABASE_URL=postgresql://postgres:ci-only@127.0.0.1:55432/ci_test uv run --frozen --extra dev --extra service pytest --junitxml=/tmp/repopilot-ci-final.xml
./scripts/service-dev.sh
python3 scripts/service-smoke.py
python3 scripts/service-restart-check.py
python3 scripts/service-crash-check.py
```

The database URL names a dedicated disposable test container, removed afterward.
Rebuilt Compose results:

- Smoke SUCCEEDED: task `e17a7ff0-e561-4f34-81c3-aa2ab9722d24`.
- All four API/PostgreSQL restart assertions passed: task `2aae7ba7-cff2-49b1-aeb7-151b20c2b9c1`.
- SIGKILL, orphan removal and successful second attempt passed: task `f39aba0d-4753-4279-8b8a-d377b5536ad9`.
- API/PostgreSQL healthy; both workers running; `git diff --check` passed.

Files changed: sandbox Git command construction, the ownership integration test,
the current README CI/test status and this report. Existing historical acceptance,
stress and evaluation artifacts were not modified. No fresh hosted run was triggered
for the correction: successful execution on the GitHub runner still needs confirmation.
The workflow itself is unchanged: it already builds the image and runs the full
suite with PostgreSQL and Docker. Fetch-depth changes or host-wide safe-directory
exceptions would not fix ownership inside the restricted child container.
