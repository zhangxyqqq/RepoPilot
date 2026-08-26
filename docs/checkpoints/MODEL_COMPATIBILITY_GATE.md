# RepoPilot V2 model/controller compatibility gate

**NOT COMPATIBLE**

> This is a 12-case synthetic protocol-interoperability gate. It is not a coding benchmark, a SWE-bench result, a leaderboard result, or evidence of general software-engineering capability.

## Preserved prior result

The frozen SWE-bench Verified behavioral pilot remains exactly **0/2 resolved** at tag `repopilot-v2-p2-behavioral` (`b1c80cb`). Its report and configuration hashes remain `7b0f9006…3c23fd6` and `e720d16d…1976f60`. Neither task was rerun, and no prior trace, prediction, report, or configuration was changed.

## Frozen protocol

The compatibility suite and admission thresholds were committed at `b7db32d` before model execution. The live run used a detached clean worktree at that commit and excluded unrelated user changes.

- Corpus: 12 tiny synthetic repositories/tasks; no SWE-bench IDs, repositories, patches, tests, or oracle data
- Coverage: 3 tool-initiation, 3 sequencing, 2 correction, 2 state-awareness, and 2 termination cases
- Provider: local Ollama 0.20.5 through the unchanged OpenAI-compatible Responses adapter
- Exact model: `mistral:7b`
- Model manifest: `sha256:6577803a…bfbc1cf`
- Profile content hash: `sha256:56812c41…a8a6f3d`
- Corpus content hash: `sha256:4111a5c2…904e5a`
- Unchanged system-prompt hash: `sha256:f310a0a4…687ee04`
- Unchanged six-tool catalog hash: `sha256:84aefeae…71546d4`
- Retrieval: structural only
- Controller: 8 iterations, 3 repair cycles, 60-second soft deadline
- Repetitions: exactly one per case; no supported seed override
- Post-result tuning: none

The canonical configuration is [model_compatibility.json](../../configs/evaluation/model_compatibility.json), and the frozen cases are [cases.v1.json](../../benchmarks/model_compatibility/cases.v1.json).

## Admission result

| Predeclared criterion | Threshold | Observed | Pass |
|---|---:|---:|---:|
| First valid tool call on tool-required cases | ≥ 90% | 0% | no |
| Protocol-complete cases | ≥ 80% | 0% | no |
| Plan-only timeout rate | 0% | 83.3% | no |
| Duplicate mutations | 0 | 0 | yes |
| Unsafe retries | 0 | 0 | yes |
| Malformed-action rate | ≤ 10% | 0% | yes |
| Successful finalization on completable cases | ≥ 80% | 16.7% | no |

The gate was not lowered after observing the result.

## Aggregate behavior

Across 45 model turns, the adapter returned 43 `plan` actions, two `final` actions, and zero model-originated tool calls. Ten cases formed plan-only loops and stopped at `total_timeout`; two ended in premature `model_final`. No case completed its required protocol milestones, and no repository file changed.

| Measure | Result |
|---|---:|
| First valid tool-call rate | 0/11 tool-required cases |
| Mean time to first valid tool call | unavailable; no call occurred |
| Plan responses before first tool call | 43 |
| Parseable structured-action rate | 100% |
| Malformed / unknown-tool / invalid-argument rate | 0% / 0% / 0% |
| Tool-selection accuracy / required coverage | 0% / 0% |
| Duplicate mutations / unsafe retries | 0 / 0 |
| Protocol-complete rate | 0/12 |
| Plan-only loop / premature final | 10/12 / 2/12 |
| Input / cached / output tokens | 30,930 / 0 / 14,447 |
| Model / tool / total latency | 813.71 s / 4.33 s / 822.88 s |

The tool-latency total is controller-owned final test/diff collection; it is not model tool use.

## Per-case result

| Case | Category | Stop | Plans / finals / tools | Protocol | Wall time | Input / output |
|---|---|---|---:|---:|---:|---:|
| `tool-init-list` | initiation | timeout | 4 / 0 / 0 | no | 60.74 s | 1,999 / 997 |
| `tool-init-search` | initiation | timeout | 5 / 0 / 0 | no | 73.52 s | 3,524 / 1,297 |
| `tool-init-read` | initiation | premature final | 2 / 1 / 0 | no | 29.06 s | 1,371 / 471 |
| `sequence-locate-read-patch` | sequencing | timeout | 3 / 0 / 0 | no | 94.57 s | 2,138 / 1,691 |
| `sequence-full-cycle` | sequencing | timeout | 3 / 0 / 0 | no | 75.48 s | 1,139 / 1,367 |
| `sequence-test-repair` | sequencing | timeout | 6 / 0 / 0 | no | 73.26 s | 4,058 / 1,285 |
| `correction-invalid-arguments` | correction | premature final | 3 / 1 / 0 | no | 48.70 s | 2,312 / 844 |
| `correction-policy-rejection` | correction | timeout | 5 / 0 / 0 | no | 62.81 s | 3,715 / 1,082 |
| `state-diff-awareness` | state | timeout | 3 / 0 / 0 | no | 100.28 s | 1,967 / 1,817 |
| `state-ambiguous-mutation` | state | timeout | 5 / 0 / 0 | no | 72.34 s | 3,515 / 1,279 |
| `termination-complete` | termination | timeout | 4 / 0 / 0 | no | 69.22 s | 2,462 / 1,227 |
| `termination-bounded-condition` | termination | timeout | 5 / 0 / 0 | no | 62.90 s | 2,730 / 1,090 |

## Evidence-based diagnosis

Observed evidence supports a **model/prompt-protocol incompatibility at tool initiation**:

- The endpoint advertised tool capability, and this model/adapter had already passed the generic Responses function-call probe used before the SWE pilot.
- The model’s text explicitly named RepoPilot tools and sometimes rendered pseudocode calls or invented observations, showing conceptual awareness without using the API function-call channel.
- Every returned action was parseable: malformed-action, unknown-tool, and invalid-argument rates were all zero. Parser rejection therefore does not explain the missing calls.
- The controller accepted each plan response and advanced normally. Security, contamination, trace reconciliation, and infrastructure checks all passed.
- With no model-originated tool observation, downstream sequencing, correction, state-awareness, and recovery competence could not be exercised.

The evidence does **not** cleanly separate base-model capability from sensitivity to RepoPilot’s unchanged prompt. A plausible but unproven hypothesis is that the instruction to emit a PLAN before editing encourages this instruction-tuned model to keep narrating or simulating tool use. Testing that requires a separately frozen future A/B experiment; it does not change this result.

## Deadline diagnosis

The 60-second value is a soft controller deadline, not a hard cancellation boundary. In ten timeout cases:

- deadline detection occurred 14.15 s late on average and 39.91 s late at maximum;
- detection followed completion of the already-running provider call by only 1.58 ms on average, 2.52 ms at maximum;
- final test/diff collection, cleanup, and artifact writing then added 425.8 ms on average, 456.1 ms at maximum;
- finalization itself averaged 363.6 ms, and cleanup after controller return stayed below 65.6 ms.

Nearly all overshoot therefore occurred in a synchronous provider call begun before the deadline. A future change should make the provider boundary cancellable or remaining-budget-aware and independently bound final collection. That change was not implemented here and must not trigger a rerun of the frozen SWE pilot.

## Security and integrity

- Sandbox assertions: **144/144 PASS**
- Contamination gates: **12/12 PASS**
- Trace/run reconciliation: **12/12 PASS**
- Infrastructure failures: **0**
- Credential leaks: **0**
- Changed repository files: **0**
- Unclassified structured errors: **0**

## Optional comparison

No second model was run. All other already-installed local models advertised completion or completion-plus-vision but not tool capability. No model was downloaded and no unsuitable comparison was used.

## Post-gate regression

- Full pytest: **165/165 PASS**
- Controlled deterministic benchmark: **12/12 tasks**, **12/12 public**, **12/12 hidden**, localization F1 **1.0**
- Deterministic reliability: **8/8**, including **6/6** recoverable scenarios; zero unsafe retries, duplicate mutations, revision divergences, budget overshoots, or unclassified errors
- Explicit MCP, Docker sandbox, trace/redaction, taxonomy, profile, and policy slice: **46/46 PASS**
- Real-world reference-integrity validation: **5/5 PASS**, with `live_agent_tasks_succeeded: null`
- P1, P2 feasibility, and frozen P2 behavioral checkpoint integrity: **PASS**
- Complete compatibility trace reading and trace/run reconciliation: **12/12 PASS**
- Credential artifact scan: **PASS**

The two frozen SWE-bench behavioral tasks were not executed during this validation.

## Durable artifacts

- Machine-readable checkpoint: [MODEL_COMPATIBILITY_GATE.json](MODEL_COMPATIBILITY_GATE.json)
- Full raw aggregate: [MODEL_COMPATIBILITY_GATE.json](MODEL_COMPATIBILITY_RUNS/MODEL_COMPATIBILITY_GATE.json)
- Per-case runs and V2 traces: [runs](MODEL_COMPATIBILITY_RUNS/runs)

## Known limitations

- Twelve deliberately easy synthetic cases measure interoperability, not coding ability.
- One repetition provides no variance estimate.
- Prelude and fault evidence are deterministic fixtures rather than naturally generated model failures.
- The current controller cannot cancel an in-flight synchronous provider request.
- Zero model tool calls prevent any conclusion about downstream correction or recovery ability.
- Isolating model capability from prompt/model interaction requires a new, independently frozen experiment.

No README, CV, benchmark case, prompt, parser, model, or preserved SWE artifact was changed after observing the result.
