# DeepSeek SWE-bench Verified Cohort 2 behavioral pilot

> Exactly one frozen attempt per instance. No reruns, substitutions, tuning, manual repair, or historical behavioral reruns occurred.

## Result

**SWE-BENCH VERIFIED COHORT 2: 3/5 RESOLVED.**

This is a 60% result only for this five-task frozen subset. It is not representative of SWE-bench Verified and is not a leaderboard comparison.

| Instance | Official outcome | Non-empty patch | F2P | P2P | Stop | Tools | Est. cost |
|---|---|---:|---:|---:|---|---:|---:|
| `mwaskom__seaborn-3187` | UNRESOLVED | no | N/A (empty patch) | N/A (empty patch) | `iteration_limit` | 32 | $0.036418 |
| `pydata__xarray-6938` | UNRESOLVED | no | N/A (empty patch) | N/A (empty patch) | `iteration_limit` | 31 | $0.035162 |
| `pylint-dev__pylint-7277` | RESOLVED | yes | 1/1 | 122/122 | `tests_passed` | 6 | $0.132266 |
| `sphinx-doc__sphinx-8551` | RESOLVED | yes | 1/1 | 32/32 | `tests_passed` | 21 | $0.056085 |
| `sphinx-doc__sphinx-9230` | RESOLVED | yes | 1/1 | 44/44 | `tests_passed` | 23 | $0.055726 |

The two empty-patch attempts passed their controller-owned environment test at the unchanged base revision, but ended at `iteration_limit`; that does not imply task success. Because there was no patch to execute, official F2P/P2P counts are unavailable and were not inferred.

## Behavior

- All 5 runs initiated a valid `list_files` tool call; there were no plan-only pre-tool loops.
- Totals: 106 model turns, 113 tool calls, 107 iterations, 0 repair cycles, and 19 unnecessary calls.
- Tool calls: `apply_patch` 3, `git_diff` 5, `list_files` 5, `read_file` 52, `run_tests` 7, `search_code` 41.
- One recoverable provider timeout occurred on Seaborn. One invalid-JSON tool-argument response occurred on Xarray; it was returned as structured evidence, but the run later exhausted iterations without a patch.
- Duplicate mutations, revision divergence, unsafe retries, and unclassified errors were all zero.
- The three successful edits changed one production file each and passed authoritative official grading.

The earlier strict `search_code`-instead-of-`list_files` deviation did not recur at initiation, and patch-without-read did not recur for any mutation. A comparable read-before-test pattern appeared in two of three resolved tasks, but these real tasks had no frozen test-first milestone and both resolved; no causal failure claim is made.

## Usage, cost, and latency

Provider-reported totals were 7,655,284 input tokens, including 7,043,968 cached and 611,316 cache-miss tokens, plus 27,816 output tokens. Reasoning tokens were unavailable.

Using the official DeepSeek `deepseek-v4-pro` prices retrieved on 2026-08-27—$0.003625/M cached input, $0.435/M cache-miss input, and $0.87/M output—the approximate cost is **$0.315657 USD**. This is a usage-based calculation, not a billing-ledger measurement; prices may change.

Aggregate agent wall time was 677.097s, including 617.477s model latency and 59.252s tool latency. These are local measurements, not production claims.

## Security and evaluation integrity

- Sandbox security: **80/80** assertions passed; contamination: **50/50** assertions passed.
- Credential literal hits: **0** in tracked files and **0** in generated artifacts; the API key remained host-side and was not forwarded to Docker.
- Official harness infrastructure failures: **0**.
- The per-run protected-test probe used a nonexistent test path, so its generic rejection is not standalone proof of the protected-test branch. That invariant remains covered by the unchanged frozen runtime, qualification evidence, and passing post-pilot Docker/policy tests.
- Observable taxonomy labels and evidence event IDs are preserved in each `run.json`. Context truncation is an efficiency signal, not an asserted root cause.

## Compatibility decisions

The frozen decisions remain separate and unchanged:

- **STRICT COMPATIBILITY: NOT COMPATIBLE — 9/12 under the frozen ≥80% gate.**
- **BEHAVIORAL ADMISSION: ADMITTED FOR SMALL BEHAVIORAL PILOT.**

The pilot admission was operationally reasonable for this bounded experiment: tools initiated on all five runs, three tasks resolved, and no security or state-discipline counter failed. This does not revise the strict compatibility verdict.

## Regression

- Full pytest: **181/181 PASS**.
- Controlled deterministic: **12/12** task/public/hidden, localization F1 1.0, 72 calls, zero unnecessary.
- Reliability: **8/8**, including 6/6 recoverable; all unsafe/state counters zero.
- Explicit MCP/security/trace/taxonomy slice: **36/36 PASS**.
- Reference integrity: **5/5 PASS**, with live-agent success intentionally null for that separate track.
- Structural retrieval remains the default; 8/8 retrieval cases retained recall@5 1.0.
- No prior checkpoint, README, CV, or historical result was modified.

## Artifacts

The JSON checkpoint contains all per-instance metrics, taxonomy evidence IDs, costs, counters, and limitations. Complete traces, run files, predictions, patches, and official grading logs are under `docs/checkpoints/DEEPSEEK_SWEBENCH_COHORT2_BEHAVIORAL_RUNS/`.

## Limitations

This is a five-task, one-attempt subset with no variance estimate. Two tasks share a repository family. Empty predictions cannot yield official F2P/P2P counts. The trusted test plans are environment checks, not the authoritative grader. No gold-patch comparison or post-hoc causal diagnosis was performed.
