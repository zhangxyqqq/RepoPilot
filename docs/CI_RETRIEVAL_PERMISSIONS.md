# Hosted Linux retrieval-fixture permissions — 2026-09-12

This follow-up changes only test-fixture preparation and verification. The previous
command-scoped Git `safe.directory` correction remains unchanged, as do production
staging, Docker configuration, retrieval code and the CI workflow.

## Evidence and exact cause

[Hosted run 34702835595](https://github.com/zhangxyqqq/RepoPilot/actions/runs/34702835595)
ran commit `07724df869cb9ebcf79ddd3c98d4f01fff7c228b`. The tests job reported
**245 passed, 1 failed, 1 warning in 98.05 seconds**. The separate Compose job passed.
The only failing test was the hardened hybrid-retrieval/no-worktree-cache test;
initialization failed with `/workspace/.git: Permission denied`.

This test used pytest's host process to create `tmp_path/repository` directly via
`mkdir`, then wrote two fixture files and passed that directory to `DockerSandbox`.
It did not use the prepared staging path used by the agent/service and most sandbox
tests. With the runner's ordinary 022 umask, the directory was 0755 and files 0644,
owned by the runner's UID/GID rather than the sandbox's 10001:10001.

The directory is a read/write bind mount at `/workspace`, but a writable mount does
not override Unix permissions. UID 10001 can read/traverse a foreign-owned 0755
folder, but cannot create `.git` in it. Git fails at `init`, before the ownership
checks addressed by the previous patch. The no-worktree-cache assertion is not the
cause: retrieval never began and its cache was not involved.

| Path | Ownership/preparation | Result |
|---|---|---|
| Host pytest retrieval fixture on native Linux | Plain runner-owned 0755 directory, directly mounted | UID 10001 cannot create `.git` |
| Other prepared sandbox/service paths | Explicit staging already supplies writable copies | Previous Git trust fix allows initialization and execution |
| Local Docker Desktop fixture | UID translation exposes the host folder as owned by sandbox UID 10001 | Owner write permission hides the Linux mismatch |
| Corrected retrieval fixture | Disposable fixture ownership transferred to 10001:10001 before sandbox start | Normal baseline initialization and hybrid retrieval succeed |

The native Linux volume regression explicitly creates ownership 1001:1001 and
0755/0644 modes, proves the legacy preparation produces the exact permission error,
then applies the same ownership preparation as the integration fixture and verifies
success. This reproduces the mismatch even when pytest runs on Docker Desktop.

## Correction and unchanged security boundary

The narrow boundary is test setup: `DockerSandbox` consumes an already-prepared
workspace. A controller-side helper changes ownership only within the disposable
fixture mount, then exits before the agent starts. The helper runs with network
none, read-only root, all capabilities dropped except CHOWN, and no-new-privileges.
It receives no Docker socket or model credentials. Cleanup restores fixture ownership
to the original host UID/GID so pytest can remove the generated Git directory.
Symlink targets are not traversed or chowned.

The agent still runs as **10001:10001**, with network none, read-only root, all
capabilities dropped and no-new-privileges. Existing integration coverage verifies
credential and mount isolation. The updated retrieval test additionally inspects
those live container properties. It retains every original retrieval, no-fallback,
ephemeral-cache, clean-diff and no-worktree-cache assertion.

There is no workspace chmod 777, root agent execution, global Git trust exception,
skip/xfail, GitHub-specific conditional or production permission change. The Linux
regression explicitly verifies 0755 directories and 0644 files after ownership
preparation. It still initializes a new Git baseline as the non-root sandbox user.
The documented production sandbox trust boundary therefore does not change.

## Verification

Full local deterministic suite: **247 passed, 0 failed, 0 errors, 0 skipped,
1 warning in 101.65 seconds**. The warning is the existing Starlette/httpx TestClient
deprecation. All sandbox, service, retrieval and regression tests ran; no paid model
calls or SWE-bench reruns were used.

```bash
REPOPILOT_TEST_DATABASE_URL=postgresql://postgres:retrieval-only@127.0.0.1:55432/retrieval_test uv run --frozen --extra dev --extra service pytest --junitxml=/tmp/repopilot-retrieval-final.xml
python3 scripts/service-smoke.py
python3 scripts/service-restart-check.py
python3 scripts/service-crash-check.py
```

The test database is a separate disposable PostgreSQL container, removed afterward.
A negative-control copy with the ownership-preparation step omitted failed the new
regression (1 failed, 1 deselected in 2.15 seconds). The unchanged regression's first
phase separately requires the exact legacy Git permission error, so neither Docker
Desktop ownership translation nor merely readable files can make the test pass.
The targeted retrieval suite passed **2 tests in 3.59 seconds**, including the
native ownership mismatch and the original DockerSandbox path.

Local Compose results (runtime images unchanged):

- Smoke succeeded: task `609d8a7e-9635-436c-b99c-ab43b9281ee0`.
- Four restart assertions passed: task `da8a9224-0839-481e-9d83-6a2f51ff2a50`.
- SIGKILL/orphan cleanup/second attempt passed: task `50eb7ba9-29d8-4912-8b47-b89e7c358ff6`.

Files changed: `tests/integration/test_retrieval_sandbox.py`, current README status,
and this report. Production files, Docker settings, the prior Git fix and workflow
are unchanged. No test was skipped or xfailed. The sandbox trust boundary is
unchanged; only disposable test setup uses ownership-setting authority. A hosted
rerun of this exact correction is still required before claiming all Actions tests
pass. The new regression requires normal Docker volume/CHOWN fixture support,
which the existing hosted ownership tests already use.
