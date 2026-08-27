# RepoPilot frozen compatibility gate — DeepSeek comparison

**NOT COMPATIBLE — 9/12 protocol-complete (75%)**

> This is a one-shot comparison on a frozen synthetic model/controller interoperability gate. It is not a coding benchmark, SWE-bench result, leaderboard result, or general model-intelligence comparison.

## Frozen execution

DeepSeek ran from detached commit `3f37e7f`, using the exact corpus, prompt, six tools, structural retrieval, controller bounds, recovery policy, sandbox, scoring rules, and thresholds previously used for `mistral:7b`.

- Cases: 12, exactly one attempt each
- Frozen profile hash: `sha256:56812c41…a8a6f3d`
- Frozen corpus hash: `sha256:4111a5c2…904e5a`
- Prompt hash: `sha256:f310a0a4…687ee04`
- Tool-catalog hash: `sha256:84aefeae…71546d4`
- Provider: official DeepSeek API, Chat Completions
- Model: `deepseek-v4-pro`
- Thinking: disabled, matching RepoPilot's previously validated tool-calling integration
- Controller: 8 iterations, 3 repairs, 60-second soft deadline
- SDK: 60-second request timeout, zero retries
- Repetitions: one; no supported seed or temperature override

No benchmark case, threshold, prompt, adapter, tool, controller behavior, recovery rule, retrieval behavior, or prior result changed after execution.

## Admission decision

| Criterion | Threshold | Observed | Pass |
|---|---:|---:|---:|
| First valid tool call | ≥ 90% | 100% (11/11) | yes |
| Protocol-complete cases | ≥ 80% | 75% (9/12) | **no** |
| Plan-only timeout rate | 0% | 0% | yes |
| Duplicate mutations | 0 | 0 | yes |
| Unsafe retries | 0 | 0 | yes |
| Malformed-action rate | ≤ 10% | 0% | yes |
| Successful finalization | ≥ 80% | 100% | yes |

The single failed admission check determines **NOT COMPATIBLE**. The threshold was not lowered.

## Aggregate behavior

| Measure | DeepSeek result |
|---|---:|
| Model turns | 38 |
| Actions | 31 tool, 7 final, 0 plan |
| Mean time to first valid tool | 2,076.1 ms |
| Plans before first tool | 0 |
| Valid structured-action rate | 100% |
| Malformed / unknown-tool / invalid-argument rate | 0% / 0% / 0% |
| Tool-selection accuracy | 74.2% |
| Required-tool coverage | 81.8% |
| Repeated calls / duplicate mutations / unsafe retries | 0 / 0 / 0 |
| Correction after structured error | 66.7% |
| Correction after failed tests | 100% |
| Premature final / plan loop / iteration limit / timeout | 0 / 0 / 0 / 0 |
| Protocol complete | 9/12 |
| Model / tool / total latency | 59.63 s / 7.40 s / 72.19 s |

## Three frozen-protocol misses

1. `sequence-locate-read-patch` used `search_code` rather than the frozen required `list_files` milestone. It then read, patched, tested successfully, and stopped.
2. `sequence-test-repair` read the implementation before executing the required initial failing test. It subsequently observed the failure, patched, retested successfully, and stopped, but violated the frozen `failed test → read → patch → passed test` order.
3. `state-diff-awareness` searched, patched, inspected `git_diff`, and finalized without the frozen required `read_file` call.

These are measured milestone mismatches. They are not infrastructure failures, and the scoring rules were not changed to forgive them.

## Tokens and calculated cost

- Input tokens: **49,733**
- Cached input tokens: **39,936**
- Cache-miss input tokens: **9,797**
- Output tokens: **2,156**
- Reasoning tokens: **unavailable (`null`)**

The [official DeepSeek price table](https://api-docs.deepseek.com/quick_start/pricing/) retrieved on 2026-08-27 listed `deepseek-v4-pro` at $0.003625/M cached-input tokens, $0.435/M cache-miss input tokens, and $0.87/M output tokens. Applying those rates to provider-reported usage gives approximately **$0.006282 USD**. This is a calculation, not an independent billing-ledger measurement; DeepSeek notes prices may change.

## Mistral vs DeepSeek protocol behavior

| Frozen-gate metric | `mistral:7b` | `deepseek-v4-pro` |
|---|---:|---:|
| Decision | NOT COMPATIBLE | NOT COMPATIBLE |
| Model turns | 45 | 38 |
| Plan / tool / final actions | 43 / 0 / 2 | 0 / 31 / 7 |
| First valid tool-call rate | 0% | 100% |
| Protocol-complete rate | 0% | 75% |
| Plan-only loop rate | 83.3% | 0% |
| Total-timeout rate | 83.3% | 0% |

DeepSeek interoperated with RepoPilot's API function-call channel and completed substantially more frozen protocols. Neither model passed the frozen admission gate. This conclusion is strictly about compatibility with this protocol; it does not show that one model is generally more intelligent or better at software engineering.

## Security and integrity

- Host-only API credential; not forwarded into Docker: **PASS**
- Credential absent from traces/reports: **PASS**
- Sandbox assertions: **144/144 PASS**
- Contamination gates: **12/12 PASS**
- Trace/run reconciliation: **12/12 PASS**
- Infrastructure failures and unclassified errors: **0**
- Six-tool catalog, sandbox, structural retrieval, frozen corpus, thresholds, and Mistral checkpoint: **unchanged**
- Frozen SWE-bench 0/2 checkpoint: **unchanged and not rerun**

## Recommended next step

Preserve this **NOT COMPATIBLE** result and stop. Do not admit this result directly to SWE-bench. If further work is desired, freeze a separate protocol-adherence study focused on tool substitution and exact milestone ordering; it must not replace or reinterpret this run.

## Post-evaluation regression

- Full pytest: **172/172 PASS**
- Controlled deterministic benchmark: **12/12 task, public, and hidden PASS**
- Deterministic reliability matrix: **8/8 PASS**
- Real-world reference integrity: **5/5 PASS**, with `live_agent_tasks_succeeded: null`
- DeepSeek trace reading and reconciliation: **12/12 PASS**
- Credential scan and frozen Mistral/SWE-bench checkpoint integrity: **PASS**

## Artifacts

- Machine-readable comparison: [MODEL_COMPATIBILITY_DEEPSEEK.json](MODEL_COMPATIBILITY_DEEPSEEK.json)
- Raw aggregate: [MODEL_COMPATIBILITY_GATE.json](MODEL_COMPATIBILITY_DEEPSEEK_RUNS/MODEL_COMPATIBILITY_GATE.json)
- Per-case runs and traces: [runs](MODEL_COMPATIBILITY_DEEPSEEK_RUNS/runs)

No README, CV, SWE-bench task, or prior compatibility artifact was modified.
