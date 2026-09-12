# Job-search milestone closeout — 2026-09-12

**Status: frozen for the current job-search milestone.** The release is a documented,
measured single-host service plus the existing coding-agent/evaluation runtime.
Hosted GitHub Actions execution remains externally unverified and is an explicit
release exception; this freeze does not turn it into a passing acceptance item.

## Scope and review disposition

The starting tree was clean at `e580e021f6f4679d9ff85d67e636431ef5100762` on
`codex/service-engineering-depth`. The review inspected the actual recent diff,
service/API/Store/worker/executor/metrics/artifact code, AgentLoop entry point,
migrations 0001 → 0002 → 0003, Compose/Docker setup, CI workflow and service tests.
It cross-checked current claims against the depth/stress JSON, prior correctness
reports and frozen evaluation checkpoints rather than using chat summaries.

No application, migration, test, Compose or CI behavior was changed during closeout.
No new P0/P1 correctness defect or P2 freeze blocker was identified. This is a review
finding within the tested boundary, not proof of absence of all defects.

Documentation corrections:

- Identified a historical evidence gap: exact MCP adapter timings and the old
  controlled DeepSeek 12-task result were reported in README history, but their raw
  artifacts were not located in tracked checkpoints. Preserved and labelled those
  figures as historical reports; excluded them from independently verified CV facts.
- Replaced the evaluation-first README opening with the complete service framing,
  engineering highlights and measurable outcomes. Preserved the detailed agent,
  retrieval, compatibility, reliability and SWE-bench sections and their results.
- Made database ownership **and** shared filesystem fencing explicit. A lease alone
  cannot establish that a paused owner stopped; submission idempotency does not
  guarantee exactly-once external effects.
- Put both throughput repetitions and the lack of general improvement near the
  top. Connection-pressure improvement is separated from execution throughput.
- Distinguished service metrics from detailed agent traces, and local artifact
  publication from storage/fencing migration. Authentication remains single-principal.
- Separated service, local CLI and evaluation startup; clarified that a complete
  no-skip regression run requires a disposable PostgreSQL database.
- Corrected the service guide's pointer to the latest measured report and made
  hosted CI's unverified status visible. Added targeted ignores for root-level
  JUnit and parallel coverage outputs without hiding preserved evidence.

## Final verification

**244 passed, 0 failed, 0 errors, 0 skipped, 1 warning in 97.36 seconds.**
The complete run includes **63 service**, **6 Docker/MCP integration**, **32 regression**
and **143 unit** tests; these categories sum to 244 and are not additional runs.
The warning is Starlette's deprecation of the current httpx TestClient integration.
No core code changed, so paid evaluation and the preserved stress matrix were not rerun.

```bash
REPOPILOT_TEST_DATABASE_URL=postgresql://postgres:closeout-only@127.0.0.1:55432/closeout uv run --frozen --extra dev --extra service pytest --junitxml=/tmp/repopilot-closeout-tests.xml
./scripts/service-dev.sh
python3 scripts/service-smoke.py
python3 scripts/service-restart-check.py
```

The PostgreSQL URL belongs to a newly created disposable container, removed after
verification. The documented Compose startup/build and smoke succeeded; all four
real API/PostgreSQL restart assertions passed. The demo data was retained, and API,
PostgreSQL and two workers remain running. The prior SIGKILL check and twelve stress
interruption results remain valid evidence against unchanged implementation hashes;
closeout's full suite also reran the Docker crash/supervisor regressions.

Hygiene checks: all 193 historical service/checkpoint evidence files remain
byte-for-byte unchanged; all 23 source checksums from the prior acceptance manifest
match. Raw scaling JSON independently totals 9984 tasks and 19968 submissions across
12 cases, with zero recorded failures or duplicate executions. Local links/anchors
in the five active closeout documents were checked; all three README/service Mermaid
diagrams parsed with Mermaid 11.12.0. Parser dependencies lived only in a temporary
validation directory, not project dependencies. External links were not network-verified.

A scan of 472 tracked files plus new documents found no current configured credentials
or common provider/GitHub/AWS/private-key patterns. Known disposable database test
passwords and placeholder examples are intentional; this scan is not a complete
forensic secret-history audit. Private `.service.env`, build/test caches and runtime
artifacts remain ignored. No accidental tracked temporary artifacts were found;
intentional negative results and checkpoints were retained. `git diff --check` passed.

[Machine-readable verification](JOB_SEARCH_CLOSEOUT.json) and
[full pytest output](JOB_SEARCH_TESTS.txt) preserve exact counts, Compose task IDs and
check results separately from earlier measurements.

## Freeze criteria

| Question | Answer |
|---|---|
| 1. Is the repository internally consistent? | Yes within the reviewed implementation/docs/evidence; explicit limits and CI exception are visible. |
| 2. Do all deterministic tests pass? | Yes: 244 passed, zero failures/errors/skips; one dependency warning. |
| 3. Is the service architecture accurately documented? | Yes: asynchronous FastAPI, real PostgreSQL task/run state, worker claims/leases, shared local execution fence, existing AgentLoop and restricted Docker. |
| 4. Are concurrency/recovery claims supported? | Yes for the tested races, interruptions and supported single-host boundary; no arbitrary-failure or exactly-once claim. |
| 5. Are scaling claims properly scoped? | Yes: local scripted workloads, both repetitions, negative measurements and connection/latency trade-offs retained; no universal worker limit. |
| 6. Are old evaluation/SWE-bench results preserved? | Yes, byte-for-byte checkpoints retained; 3/5 Cohort 2, earlier failed/stopped gates, negative retrieval promotion and strict 9/12 compatibility remain distinct. |
| 7. Any remaining P0/P1 correctness issues? | None identified by this review or final deterministic verification within the stated scope. |
| 8. Any P2 issue materially weakening the job-search story? | No identified freeze blocker. Hosted CI is an evidence gap and explicit exception, not a claimed pass. |
| 9. Is a current README claim stronger than its evidence? | None identified after narrowing claims; limitations accompany both service and model results. |
| 10. Ready to freeze? | Yes for this job-search milestone, with hosted CI externally unverified and the documented operational non-goals. |

## Retained limitations and external verification

The supported execution boundary is one host, a trusted Docker daemon and shared
POSIX artifact/lock storage. No multi-tenant authorization, exactly-once remote
side effects, running cancellation, disk/history retention policy, TLS deployment,
backup/restore drill, long-duration soak or production capacity is established.
Metrics aggregate retained history on scrape; admission serializes and counts
active tasks. Future multi-host or materially larger workload requirements would
need fresh evidence and execution-fencing design, not automatic broker adoption.
Those directions are outside this frozen milestone.

The Actions workflow is internally configured for frozen uv dependencies, Python
3.12, PostgreSQL 16, real Docker regression and Compose smoke/restart/crash jobs.
Hosted execution was not retried: the earlier push was rejected because the PAT
lacked `workflow` scope; no remote branch or hosted run was created. The local
freeze is therefore not a published GitHub release or a verified CI badge.

No paid model calls, SWE-bench reruns, CV edits or infrastructure/features were
performed. Historical benchmark files were not retuned or overwritten.

## Evidence navigation

- [Engineering evidence summary](JOB_SEARCH_ENGINEERING_SUMMARY.md)
- [Interview drill-down map](INTERVIEW_ENGINEERING_MAP.md)
- [Current depth measurements and limitations](SERVICE_DEPTH.md)
- [Earlier correctness audit](SERVICE_AUDIT.md)
- [Earlier stress evidence](SERVICE_STRESS.md)
- [Frozen Cohort 2 behavioral report](checkpoints/DEEPSEEK_SWEBENCH_COHORT2_BEHAVIORAL.md)
