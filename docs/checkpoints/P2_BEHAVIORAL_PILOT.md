# RepoPilot V2 P2 SWE-bench Verified behavioral pilot

**BEHAVIORAL PILOT COMPLETE — 0/2 RESOLVED**

> This is a two-instance, one-attempt-per-instance pilot. It is not a representative SWE-bench benchmark, a leaderboard result, or evidence of broad real-world coding-agent performance.

## Frozen protocol

The behavioral configuration was committed at `c67ff06` before either live attempt. Both attempts ran from a detached clean worktree at that commit, excluding unrelated user changes. No prompt, tool description, budget, recovery rule, retrieval configuration, environment, or task selection changed between the two runs or after either outcome.

- Instances: `pallets__flask-5014`, `pytest-dev__pytest-10051`
- Provider: local Ollama 0.20.5 through RepoPilot's OpenAI-compatible Responses adapter
- Exact model: `mistral:7b`
- Model manifest: `sha256:6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf`
- Model form: 7.2B parameters, GGUF Q4_K_M
- Profile canonical hash: `sha256:1e1c57e076515772c0caabfe7faf0b51427c8a589283dff55c6fd946d250bfd2`
- System-prompt hash: `sha256:f310a0a46501849ed020e8165a741163c14fac81501c595901e5850c8687ee04`
- Six-tool catalog hash: `sha256:84aefeae76e6b1e6f48a04657765d7aa8b7216d9f55c9549f971ed32c71546d4`
- Retrieval: structural only; semantic and hybrid retrieval disabled
- Controller: 30 iterations, 3 repair cycles, 300-second total deadline
- Recovery: 2 provider retries, 1 safe-read retry, 0 test-timeout retries
- Repetitions: exactly 1 per instance; no supported seed override

The full data-only profile is [swebench_verified_behavioral_pilot.json](../../configs/evaluation/swebench_verified_behavioral_pilot.json).

## Official outcome

The two exact predictions were submitted once to SWE-bench 5.0.2 at harness commit `7a21e05772954cc81471ae19d56f436cecf43c54`, using dataset revision `78f471bf655a3137b2e8a75af1501690ec009ec3` and the same pinned AMD64 images validated during feasibility.

| Instance | Base commit | Prediction | F2P | P2P | Official resolved | Outcome |
|---|---|---|---|---|---:|---|
| `pallets__flask-5014` | `7ee9ceb71e86…` | empty | not run | not run | no | unresolved |
| `pytest-dev__pytest-10051` | `aa55975c7d3f…` | empty | not run | not run | no | unresolved |

The harness invocation succeeded with zero harness errors and zero infrastructure failures. SWE-bench classifies empty predictions separately and does not execute per-instance FAIL_TO_PASS or PASS_TO_PASS tests for them. The test outcomes are therefore **not run/unavailable**, not inferred failures. At the pilot layer, an empty prediction cannot resolve a task, so both outcomes are unresolved.

## Agent behavior

| Instance | Stop | Iterations / model turns | Model actions | Tool calls | Repairs | Changed files | Taxonomy |
|---|---|---:|---|---:|---:|---:|---|
| Flask 5014 | `total_timeout` | 19 / 18 | 18 plan, 0 tool, 0 final | 2 controller-owned | 0 | 0 | `controller.total_timeout` |
| pytest 10051 | `total_timeout` | 15 / 14 | 14 plan, 0 tool, 0 final | 2 controller-owned | 0 | 0 | `controller.total_timeout` |

Across both tasks, the model produced plan text on all 32 turns and never invoked a repository tool. Consequently it never inspected the repository through the six tools, never applied a patch, and never changed a file. The controller's final `run_tests` and `git_diff` calls account for all four tool calls.

The controller-owned public test files passed at revision zero (Flask: 59 tests; pytest: 15 tests). That explains the local `success: true` field in the raw RepoPilot run artifact, but it is neither evidence of a fix nor official resolution. The official harness result remains authoritative.

Changed-file localization has recall 0 and F1 0 for both tasks. Precision is undefined for an empty prediction. Expected-fix metadata was consulted only after both attempts finished.

## Performance and reliability

| Instance | Controller wall | Model latency | Tool / test latency | Input / cached / output tokens | Deadline overshoot |
|---|---:|---:|---:|---:|---:|
| Flask 5014 | 314.3 s | 312.7 s | 1.50 / 1.10 s | 46,357 / 0 / 5,565 | 14.3 s |
| pytest 10051 | 331.9 s | 330.6 s | 1.23 / 0.85 s | 40,002 / 0 / 6,327 | 31.9 s |
| **Total** | **646.1 s** | **643.4 s** | **2.73 / 1.95 s** | **86,359 / 0 / 11,892** | **2 cases** |

- Reasoning tokens reported: 0
- Monetary cost: not calculated; the provider was local and unmetered
- Unsafe retries: 0
- Duplicate mutations: 0
- Revision divergences: 0
- Recovery events: 0
- Rejected calls: 0
- Unclassified structured errors: 0
- Trace/run reconciliation: PASS for both runs

The 300-second controller deadline is soft between calls, and final controller-owned test/diff collection still runs. In-flight provider calls plus finalization caused the measured 14.3-second and 31.9-second overshoots. This is a reliability limitation to address in a future phase; it did not relax the sandbox boundary.

## Runtime security and contamination

Both cases passed all 16 runtime security assertions immediately before model execution: network disabled, UID/GID `10001:10001`, read-only root, all capabilities dropped, `no-new-privileges`, bounded memory/CPU/PIDs, only the staged worktree writable, and no original repository, Docker socket, host home, SSH agent, or credentials mounted or forwarded.

Both cases also passed all 10 contamination assertions immediately before their first model call. The checks covered the staged worktree, prompt, structural repository context, sampled read-only tool outputs, tool schemas, container environment names, mounts, and the exact six-tool surface. Reference/test patches and oracle metadata were not exposed.

Aggregate: security **32/32 PASS**; contamination **20/20 PASS**.

## Post-pilot regression

- Full pytest: **155/155 PASS** after adding two checkpoint-integrity assertions (153/153 before live execution)
- Controlled deterministic benchmark: **12/12 tasks**, **12/12 public**, **12/12 hidden**, localization F1 **1.0**
- Deterministic reliability: **8/8**, with unsafe retries, duplicate mutations, revision divergences, budget overshoots, and unclassified errors all zero
- Explicit MCP, Docker sandbox, trace/redaction, taxonomy, and structural-default slice: **26/26 PASS**
- Existing real-world reference-integrity semantics: **5/5 PASS**, still reporting `live_agent_tasks_succeeded: null`
- P1 negative-result checkpoint: byte-for-byte unchanged from `faa2faa`
- Credential artifact scan, post-run oracle contamination scan, complete trace reading, and trace/run reconciliation: **PASS for both runs**

## Durable artifacts

- Frozen profile: [swebench_verified_behavioral_pilot.json](../../configs/evaluation/swebench_verified_behavioral_pilot.json)
- Official predictions: [predictions.jsonl](P2_BEHAVIORAL_RUNS/predictions.jsonl)
- Official harness report: [official-harness-report.json](P2_BEHAVIORAL_RUNS/official-harness-report.json)
- Flask run: [run.json](P2_BEHAVIORAL_RUNS/pallets__flask-5014/run.json), [trajectory.jsonl](P2_BEHAVIORAL_RUNS/pallets__flask-5014/trajectory.jsonl), [preflight.json](P2_BEHAVIORAL_RUNS/pallets__flask-5014/preflight.json)
- pytest run: [run.json](P2_BEHAVIORAL_RUNS/pytest-dev__pytest-10051/run.json), [trajectory.jsonl](P2_BEHAVIORAL_RUNS/pytest-dev__pytest-10051/trajectory.jsonl), [preflight.json](P2_BEHAVIORAL_RUNS/pytest-dev__pytest-10051/preflight.json)
- Machine-readable aggregate: [P2_BEHAVIORAL_PILOT.json](P2_BEHAVIORAL_PILOT.json)

## Known limitations

- Two instances and one repetition provide no statistically meaningful performance estimate or variance measurement.
- Empty predictions mean no official per-instance test execution and no F2P/P2P pass counts.
- The local quantized 7.2B model is not representative of frontier coding-agent models. It was frozen after the configured OpenAI credential failed a pre-run access check.
- The controller deadline is not a hard process-level kill boundary.
- Localization precision is undefined for empty changed-file sets.
- Official SWE-bench reporting keeps empty patches outside its completed/unresolved buckets; this pilot maps them to unresolved because they cannot satisfy the resolution criterion.

No README, CV, or portfolio claim was changed.
