# RepoPilot V2: Agent Engineering & Evaluation Platform

Status: proposed architecture; no V2 implementation is included in this document.

## 1. Executive summary

RepoPilot V1 is a deliberately narrow, well-bounded repository coding agent. Its strongest portfolio evidence is not the breadth of its feature list; it is the fact that one controller, six typed tools, a restricted Docker boundary, append-only trajectories, hidden-test evaluation, and separate deterministic/live/reference-integrity tracks form a coherent evaluated system.

V2 should preserve that shape and add four platform capabilities first:

1. a versioned trace model and local trace analytics;
2. a configurable, evidence-based failure taxonomy;
3. an MCP-compatible stdio adapter over the same six-tool registry;
4. deterministic failure injection plus explicit, bounded recovery policies.

Hybrid structural/lexical/embedding retrieval should follow once the observability and evaluation contracts can measure it. Live SWE-bench Verified behavioral evaluation should remain an optional final track and should ship only if the official per-instance environments can be wrapped without weakening RepoPilot's sandbox controls.

The requested priority buckets are therefore sound, but the implementation order within P0 should change. Trace schema and taxonomy must precede MCP and reliability testing because the latter need stable tool-result, error, retry, and span semantics. MCP remains P0, but it is not the first implementation task.

V2 is an **Agent Engineering & Evaluation Platform**, not a production service. It remains local, single-agent, run-scoped, file-artifact based, and intentionally small.

## 2. Repository-wide V1 audit

### 2.1 Current control and data flow

```text
CLI
  -> provider factory -> ModelClient
  -> run_agent
       -> filtered repository staging
       -> DockerSandbox
       -> ToolRegistry (six public typed tools)
       -> AgentLoop
            <-> ModelClient.next_action
            -> ToolRegistry.call
                 -> DockerSandbox.invoke
                      -> sandbox_runner.py
            -> TrajectoryRecorder (JSONL)
       -> run.json

evaluation runner
  -> 12 controlled cases
  -> public tests during agent run
  -> fresh hidden-test sandbox
  -> localization/efficiency metrics
  -> JSON and Markdown reports

real-world validator
  -> 5 pinned SWE-bench Verified definitions
  -> checkout/commit/patch/compile integrity only
  -> official-format gold predictions
```

### 2.2 Strengths that V2 must preserve

- `AgentLoop` owns iteration, repair, timeout, final-test, and final-diff behavior. Model claims do not determine success.
- `ToolRegistry` validates model arguments before sandbox execution, tracks workspace revisions, identifies exact duplicate calls, and treats rejected/no-op calls as unnecessary.
- `TOOL_SCHEMAS` exposes only `list_files`, `search_code`, `read_file`, `apply_patch`, `run_tests`, and `git_diff`. The model never supplies raw shell commands.
- `DockerSandbox` stages a copy rather than mounting the input repository. It uses no network, a non-root user, a read-only root filesystem, dropped capabilities, `no-new-privileges`, and CPU/memory/process/time limits.
- Host-side and container-side path checks reject traversal, sensitive paths, symlinks, arbitrary commands, test edits, and unsupported patch behavior.
- `build_repository_context` provides a deterministic, issue-ranked Python AST outline with bounded file/symbol/character budgets and stable tie breakers.
- `TrajectoryRecorder` writes append-only JSONL, while `run.json`, `evaluation.json`, and Markdown reports provide human- and machine-readable artifacts.
- The 12 controlled cases keep public tests, hidden tests, expected localization, and reference patches separate from the staged agent workspace.
- Deterministic scripted evaluation validates infrastructure but is explicitly not presented as model intelligence. Live-provider evaluation is saved separately.
- The five real-world task definitions validate pinned SWE-bench references without claiming behavioral success.

### 2.3 Architectural limitations relevant to V2

| Area | Current state | V2 limitation exposed |
|---|---|---|
| Tool contracts | JSON-like schemas and validation live in `tools/contracts.py`; execution lives in a Docker-specific `ToolRegistry`; container dispatch has its own `TOOLS` table | There is no transport-neutral tool definition/result interface, and host/container registration can drift |
| Controller | Model errors terminate the run; tool errors are returned to the model; failed-test repair cycles are counted | Recovery is implicit and incomplete; rejected patches, provider failures, timeouts, and ambiguous mutation outcomes have no explicit policy |
| Trajectories | Schema version 1 events contain flexible payloads and sequence numbers | There are no stable event IDs, spans, parent relationships, normalized statuses/error codes, readers, validation, or trace-derived analytics |
| Evaluation | Success, test counts, localization, tool calls, repair cycles, latency, tokens, and stop reason are reported | Failures are not classified consistently; aggregate means hide distributions; no recovery or retrieval metrics exist |
| Retrieval | `list_files` returns file enumeration plus an issue-ranked AST outline; `search_code` is literal/regex only | No semantic matching, no explicit ranked candidate records/scores, and no controlled structural/semantic/hybrid comparison |
| Process model | Each sandbox tool call starts `sandbox_runner.py` through `docker exec` | Any embedding index must be bounded and cached in an ephemeral container location or it will be rebuilt on each call |
| CLI | `run`, `eval`, and `real-validate` | No trace analysis, evaluation profiles, reliability suite, retrieval comparison, MCP server, or behavioral real-world command |
| Providers | A common `ModelClient` protocol supports scripted, OpenAI Responses, compatible Responses, and DeepSeek Chat Completions | Provider exceptions are generic and do not communicate retryability or failure stage |
| Controlled benchmark | Deterministic scripts know the target file and reference patch | Excellent infrastructure validation, but inappropriate evidence for retrieval quality or autonomous recovery behavior |
| SWE-bench track | Exact revision/reference/test-patch integrity and compile checks | No agent-generated prediction grading; the generic sandbox image cannot reproduce per-repository dependencies |

### 2.4 Current test and benchmark coverage

The current suite covers tool argument validation, provider translation and credential separation, controller stop-on-pass behavior, patch/test protections, staging, path policy, repository-map structure/ranking/determinism, JSONL append behavior, Docker security flags, benchmark corpus integrity, deterministic end-to-end evaluation, and real-world metadata integrity.

The existing controlled corpus can be reused for:

- no-regression checks for all P0 modules;
- trace/taxonomy backfills and analytics;
- deterministic fault schedules around known-good scripted actions;
- retrieval sanity checks and end-to-end non-regression;
- live V1-versus-V2 comparisons under fixed provider/model/budgets.

It cannot, by itself, prove semantic retrieval value because several issues contain strong lexical or symbolic clues and the deterministic model explicitly reads the expected file and applies the gold patch. A separate retrieval-challenge corpus is required.

## 3. V2 design principles and invariants

1. **One agent remains the decision maker.** V2 adds interfaces, measurements, and reliability policies, not multi-agent orchestration.
2. **The six tools remain the only model-visible repository capabilities.** MCP is a transport adapter; hybrid retrieval remains behind `list_files`.
3. **One canonical tool catalog.** Names, descriptions, JSON Schemas, mutation classification, and result/error semantics must come from one definition used by direct calls, provider adapters, MCP, and tests.
4. **The sandbox remains authoritative.** MCP, retrieval, observability, and fault injection cannot bypass staging, path validation, command allowlisting, test protection, or Docker controls.
5. **Observed facts and inferred causes remain distinct.** A timed-out call is observable; “the model chose a bad strategy” may be a hypothesis. Reports must not present the latter as certain.
6. **Run artifacts remain the storage layer.** JSONL/JSON/Markdown are sufficient. No database, service, or UI is justified.
7. **Evaluation tracks remain explicit.** Deterministic infrastructure validation, controlled live-model behavior, retrieval/reliability experiments, SWE-bench reference integrity, and SWE-bench behavioral grading must not be merged into one success rate.
8. **Optional dependencies must earn their cost.** MCP and embedding dependencies are isolated extras or pinned image components. No technology is added only for résumé vocabulary.
9. **Backward compatibility is tested, not assumed.** V1 trajectories remain readable, the default direct tool path remains available, and the 12 controlled cases remain unchanged historical fixtures.
10. **No production claims.** Metrics describe the tested local workloads and selected models only.

## 4. Target V2 architecture

```text
                         +----------------------+
                         | Evaluation profiles  |
                         | taxonomy + thresholds|
                         +----------+-----------+
                                    |
CLI -> RunSession ------------------+-----------------------------+
       |                                                          |
       +-> AgentLoop -> RecoveryPolicy -> ModelClient              |
       |       |                |                                  |
       |       |                +-> optional FaultInjector         |
       |       v                                                   |
       |   ToolBackend protocol                                    |
       |       |                                                   |
       |       +-> DirectToolBackend -> canonical ToolRegistry ----+
       |       |                                                   |
       |       +-> MCP stdio adapter -> canonical ToolRegistry     |
       |                                                           |
       +-> DockerSandbox -> six tools                              |
       |       |
       |       +-> list_files -> ContextSelector                   |
       |                          structural | semantic | hybrid    |
       |                                                           |
       +-> versioned TraceRecorder -> trajectory.jsonl             |
                                  -> TraceReader/Analytics          |
                                  -> taxonomy classifier           |
                                  -> JSON/Markdown reports          |
```

`RunSession` in this diagram is a small lifecycle abstraction, not a service. It owns staged workspace, sandbox, registry, recorder, and cleanup for one local run. It avoids duplicating lifecycle code between `run_agent` and an MCP stdio entry point.

## 5. Priorities and recommended order

| Priority | Module | Why now |
|---|---|---|
| P0.1 | Versioned structured traces and local analytics | Foundation for every later comparison and recovery claim |
| P0.2 | Evaluation profiles and failure taxonomy | Gives stable, testable semantics to failures before injecting or recovering from them |
| P0.3 | Canonical tool contracts and MCP-compatible stdio adapter | High interoperability value with low security risk when kept local and registry-backed |
| P0.4 | Failure injection and bounded recovery evaluation | Demonstrates reliability engineering with deterministic evidence |
| P1.1 | Hybrid structural/lexical/embedding context selection | Valuable but adds dependency, latency, caching, and benchmark complexity |
| P1.2 | Controlled retrieval baseline experiment | Required to justify P1.1; must be designed before implementation and run after it |
| P2 | Small SWE-bench Verified behavioral track | Strong external validity, but environment compatibility and cost make it a gated stretch feature |

This retains the user's P0/P1/P2 hypothesis. The only challenge is sequencing: MCP should not be the first code change. First establish stable trace, error, and taxonomy contracts; then expose the registry through MCP; then add fault/recovery behavior whose evidence is captured by those contracts.

## 6. P0.1 — Versioned structured trace and observability layer

### A. Motivation

#### V1 limitation

V1 records useful raw events, but their payloads are event-specific dictionaries. There is no validated reader, event identity, span/parent relationship, normalized outcome, stable error code, or trace-derived breakdown. `run.json` and evaluation reports summarize controller state directly rather than proving that the same values can be reconstructed from the trajectory.

#### General agent-engineering problem

Agent systems are asynchronous-looking sequences of model decisions, tool calls, state changes, retries, and evaluations even when implemented in one process. Debugging them requires causal correlation: which model turn requested which tool, which revision it observed, what failed, what retried, and how latency/tokens accumulated. This is the same observability problem encountered in larger agent systems, addressed here without introducing a telemetry service.

### B. Proposed architecture

#### Existing components touched

- `trajectory/recorder.py`
- `agent/loop.py`
- `models.py`
- `tools/registry.py`
- provider adapters for normalized provider-error metadata
- `evaluation/runner.py`, `metrics.py`, and `reports.py`
- CLI for a local `trace-summary` command

#### New modules/interfaces

- `trajectory/schema.py`: typed event envelope, event/status/error enums, schema validation, V1-to-V2 normalization.
- `trajectory/reader.py`: streaming JSONL reader that rejects malformed sequence/order/schema data with line numbers.
- `trajectory/analytics.py`: pure functions that derive run, model, tool, revision, retry, latency, token, and failure summaries.
- `TraceRecorder` protocol, with the existing JSONL recorder as the only initial implementation.

Every V2 event should contain a common envelope:

```json
{
  "schema_version": 2,
  "run_id": "...",
  "event_id": "...",
  "sequence": 7,
  "timestamp": "...",
  "type": "tool_call_finished",
  "phase": "tool",
  "status": "ok|error|timeout|rejected|cancelled",
  "span_id": "...",
  "parent_span_id": "...",
  "iteration": 3,
  "workspace_revision_before": 0,
  "workspace_revision_after": 1,
  "duration_ms": 41.2,
  "payload": {},
  "error": null
}
```

Model turns, tool calls, controller finalization, hidden evaluation, and injected faults become spans or span-linked events. `origin` remains explicit (`model`, `controller`, `recovery_policy`, `evaluation`). Error objects use stable `code`, `stage`, `retryable`, and sanitized `message` fields while retaining a bounded raw provider/sandbox message only when safe.

The recorder remains append-only. Analytics stream JSONL and write derived JSON/Markdown beside the run; they do not mutate the source trace. Existing V1 traces are read through a compatibility normalizer and marked `source_schema_version: 1`; they are never rewritten silently.

No OpenTelemetry collector, tracing backend, database, or dashboard is added. Export to a standard telemetry format can be reconsidered only if a concrete external consumer appears.

#### Data/control flow

1. The controller opens a run span.
2. Each model request creates a child span with provider/model, latency, usage, action type, and normalized failure.
3. Each accepted action creates a tool span linked to iteration and workspace revision.
4. Registry validation, sandbox execution, retries, and revision changes produce structured child events.
5. Public/hidden evaluation spans are linked to the originating run but retain distinct evaluation-track fields.
6. `TraceReader` validates the completed or partial JSONL stream.
7. `TraceAnalytics` derives report metrics; cross-checks compare derived totals to `run.json`.

#### Security implications

- Continue bounding observations and patches before recording.
- Add key-name redaction for credentials/tokens and a regression test covering provider exceptions and metadata.
- Record repository path as a configurable redacted identifier in shareable reports; keep the local absolute path only in private run metadata if required.
- Never record environment dictionaries, API-key values, Docker inspect output containing unrelated host data, or model SDK objects.
- Trace IDs add correlation, not authorization; no remote trace endpoint is opened.

### C. Acceptance criteria

- Every V2 run emits schema-valid, strictly increasing JSONL events with unique event IDs and valid parent references.
- A partial trace ending mid-run remains readable and is reported as incomplete rather than corrupt.
- All existing V1 trajectory fixtures remain readable through the compatibility layer.
- Tool latency, model latency, total tokens, tool count, retries, revision count, final status, and stop reason are reconstructable from V2 JSONL and match `run.json`.
- No configured fake credential value appears in trajectory, derived JSON, Markdown, or exception text.
- The deterministic 12-case suite still passes with the same public/hidden/localization outcomes.
- `trace-summary <trajectory.jsonl>` produces deterministic JSON and Markdown without Docker or network access.
- Unit tests cover malformed JSON, unknown schema versions, duplicate/out-of-order sequences, missing parents, null provider usage, and bounded errors.

Successful implementation means a reviewer can answer “where time/tokens/actions went and what failed” from trajectory artifacts alone.

### D. Evaluation plan

#### Metrics

- model/tool/controller/evaluation latency totals plus p50/p95 across cases;
- provider token fields and tokens per successful task;
- calls by tool, status, origin, and revision;
- retries and recovery latency;
- observation/patch truncation counts;
- trace completeness and schema-validation rate;
- reconciliation error count between trace-derived and `run.json` totals.

#### Baseline versus V2

Replay existing V1 trajectories through the compatibility reader and compare the metrics currently available. Run the unchanged deterministic corpus under V2 and require metric reconciliation. The comparison is about observability coverage and correctness, not task-success improvement.

#### Reused cases and new fixtures

All 12 controlled cases and current provider fakes are reusable. New small JSONL fixtures are required for partial, malformed, redacted, retry, timeout, and V1 compatibility cases.

### E. Risks / trade-offs

- More event types and IDs increase artifact size and implementation surface.
- Incorrect nesting can double-count latency; analytics must specify inclusive versus exclusive duration.
- Raw observations can contain repository secrets not excluded by filename; bounded recording is not complete data-loss prevention.
- A local trace schema is not equivalent to production distributed tracing. Avoid claiming OpenTelemetry or production observability.
- Backward compatibility adds code that must eventually be sunset explicitly.

### F. CV/interview value

Demonstrates event-schema design, causal tracing, telemetry normalization across model/tool/controller boundaries, safe artifact handling, streaming analytics, and backward compatibility. After implementation, a candidate should be able to explain why spans and stable errors improve agent debugging, how latency is aggregated without double counting, how traces are redacted, and why local JSONL analytics were a better fit than a telemetry stack.

## 7. P0.2 — Configurable evaluation profiles and failure taxonomy

### A. Motivation

#### V1 limitation

V1 exposes stop reasons and an “unnecessary tool call” count, but does not classify failures across setup, model, tool, retrieval, edit, test, controller, and evaluation stages. `repair_cycles` counts only failed tests on a modified revision; a rejected patch recovered on the next call is not a repair cycle. Evaluation settings are mostly CLI defaults rather than named, versioned profiles.

#### General agent-engineering problem

Aggregate success rates do not explain why agents fail or whether two systems fail differently. Evaluation systems need stable taxonomies, reproducible configurations, and evidence provenance. They also need to avoid converting weak heuristics into confident root-cause claims.

### B. Proposed architecture

#### Existing components touched

- `config.py`, `models.py`, and `cli.py`
- `agent/loop.py` and `tools/registry.py`
- `evaluation/cases.py`, `runner.py`, `metrics.py`, and `reports.py`
- V2 trace schema and analytics

#### New modules/interfaces

- `evaluation/profiles.py`: validated, versioned evaluation profile loader.
- `evaluation/taxonomy.py`: taxonomy definitions, rule evaluator, and classification result types.
- `configs/evaluation/default.json`: canonical controlled-evaluation profile.
- `configs/evaluation/failure_taxonomy.v1.json`: labels, descriptions, phases, severity, default recoverability, and rule mappings.

Profiles specify benchmark track, model repetitions, limits, retrieval strategy, fault scenario, taxonomy version, required metrics, and acceptance thresholds. JSON is preferred over adding YAML solely for configuration.

The taxonomy records multiple dimensions instead of forcing one root cause:

| Dimension | Examples |
|---|---|
| Phase | setup, model, tool, retrieval, edit, test, controller, evaluation |
| Observable signal | sandbox start error, malformed action, invalid arguments, rejected patch, timeout, public fail, hidden fail, limit reached |
| Recoverability | recovered, retryable-unrecovered, non-retryable, unknown |
| Outcome impact | informational, efficiency degradation, task failure, harness failure |
| Attribution confidence | deterministic rule, benchmark-oracle rule, manual hypothesis |

Minimum built-in labels include:

- `setup.staging_failed`, `setup.sandbox_start_failed`;
- `model.provider_error`, `model.malformed_output`, `model.invalid_action`, `model.premature_final`;
- `tool.unknown_tool`, `tool.invalid_arguments`, `tool.duplicate_call`, `tool.execution_timeout`, `tool.execution_error`;
- `policy.path_rejected`, `policy.test_edit_rejected`, `policy.command_rejected`;
- `retrieval.expected_file_missed`, `retrieval.context_truncated`;
- `edit.patch_rejected`, `edit.no_op`, `edit.overbroad_change`;
- `test.public_failed`, `test.public_pass_hidden_fail`, `test.timed_out`;
- `controller.iteration_limit`, `controller.repair_limit`, `controller.total_timeout`;
- `evaluation.reference_integrity_failed`, `evaluation.harness_failed`.

Labels are derived from structured facts. For example, `public_pass_hidden_fail` is certain given results; “retrieval caused hidden failure” is not inferred automatically. Manual review annotations, if used, are stored separately with author/source and never replace deterministic labels.

#### Data/control flow

1. CLI loads and validates a named profile before starting a run.
2. The resolved profile, including defaults and a content hash, is written to `run_started`.
3. Structured trace events are evaluated by deterministic taxonomy rules.
4. Case reports include signals, recoverability, evidence event IDs, and optional manual annotations.
5. Aggregate reports show failure incidence by label and track, never mixing harness failures into agent task failures.

#### Security implications

- Configuration is data only: no Python import paths, shell fragments, expressions, or arbitrary commands.
- Test commands remain controller-selected and allowlisted; profiles cannot override sandbox policy.
- Unknown fields and labels fail closed.
- Taxonomy messages reference sanitized trace evidence rather than copying unbounded exception text.

### C. Acceptance criteria

- Profile and taxonomy JSON are schema-validated; unknown fields, duplicate labels, invalid thresholds, and unsupported taxonomy versions fail before sandbox startup.
- Every controller stop reason and every structured error event maps to at least one observable taxonomy label or an explicit `unclassified` counter.
- A gold-labeled unit fixture set achieves 100% deterministic rule agreement.
- Multiple simultaneous signals are preserved; classification never overwrites earlier evidence.
- Reports distinguish agent failure, infrastructure/harness failure, recovered failure, and efficiency-only degradation.
- Reprocessing the same trace/profile yields byte-stable classification JSON apart from declared timestamps/paths.
- The existing 12-case deterministic report remains 12/12 and adds zero false task-failure labels.

Successful implementation means failure distributions can be compared between runs without reading free-form logs and without overstating causal attribution.

### D. Evaluation plan

#### Metrics

- label frequency per task/run/track;
- recovery rate by label;
- unclassified error-event rate;
- public-pass/hidden-fail rate;
- failure stage distribution;
- efficiency degradations per successful task;
- terminal reason versus taxonomy consistency;
- repeated-live-run outcome variance.

#### Baseline versus V2

Back-classify the current deterministic and saved live trajectories where V1 data permits. Report unavailable evidence explicitly. Compare V1’s stop-reason-only view with V2’s label coverage; do not reinterpret missing V1 fields as absence of failure.

#### Reused cases and new fixtures

All current controlled cases and the saved recovered patch-rejection live trajectory can be reused. New synthetic classification fixtures are required for every built-in label, including infrastructure failures that should not count against agent behavioral success.

### E. Risks / trade-offs

- Taxonomies can become bloated or unstable. Version labels and require a demonstrated analysis use before adding one.
- Heuristic labels can look like causal explanations. Reports must retain evidence and confidence.
- Configuration flexibility can reduce comparability if every run uses a different profile; canonical profiles and hashes address this.
- More metrics increase multiple-comparison and cherry-picking risk. Predeclare primary metrics per experiment.

### F. CV/interview value

Demonstrates evaluation design, reproducibility, typed configuration, error ontology design, evidence provenance, and careful separation of agent versus infrastructure failure. A candidate should be able to discuss taxonomy granularity, label versioning, multi-label failures, deterministic versus manual attribution, and why one success rate is insufficient.

## 8. P0.3 — MCP-compatible adapter over the canonical typed tool registry

### A. Motivation

#### V1 limitation

The six tool schemas are passed directly to provider adapters, and `ToolRegistry` is called directly by `AgentLoop`. This works internally but cannot be exercised by a standard MCP client. The registry also depends concretely on `DockerSandbox`, which makes parity testing and alternative transports harder than necessary.

#### General agent-engineering problem

Tool interoperability requires a transport protocol without duplicating business logic or security policy. The meaningful engineering work is not “adding MCP”; it is proving that direct provider tools and MCP calls share the same contracts, results, mutation semantics, and sandbox controls.

### B. Proposed architecture

#### Existing components touched

- `tools/contracts.py`, `tools/registry.py`, and `tools/__init__.py`
- `agent/loop.py` type hints
- `sandbox/docker.py`
- provider adapters, which continue consuming the canonical JSON Schemas
- `cli.py`, `pyproject.toml`, and tests

#### New modules/interfaces

- `tools/backend.py`: a narrow `ToolBackend` protocol exposing `definitions`, `call`, `revision`, and efficiency counters.
- `tools/definitions.py` or an evolved `contracts.py`: immutable `ToolDefinition` records containing name, description, input schema, read-only/mutating classification, and public visibility.
- `mcp/server.py`: local stdio MCP server backed by one `RunSession` and the existing registry.
- `mcp/adapter.py`: conversion between RepoPilot definitions/results and MCP `tools/list`/`tools/call` structures.
- `session.py`: one-run staging/sandbox/registry/recorder lifecycle shared by `run_agent` and MCP serving.

The initial MCP feature is **stdio only**. `repopilot mcp-serve <repository> --issue ... --output ...` stages the repository, starts the same restricted Docker sandbox, exposes exactly six public tools, and cleans up when stdin closes. It does not expose `_init_repo`, raw Docker methods, run directories, host paths, or arbitrary test commands.

The internal agent continues using the direct backend initially. This keeps MCP an interoperability layer rather than forcing a transport round trip into every normal run. A later evaluation may compare direct and MCP-backed controller paths, but behavior parity is required before making MCP the default.

MCP results carry both bounded human-readable content and a structured result envelope. Registry validation errors map to structured tool errors without losing `revision`, `error_code`, or `retryable`. MCP request IDs are correlated to trace span IDs but are not reused as internal security identifiers.

#### Data/control flow

```text
MCP client
  -> stdio initialize
  -> tools/list -> canonical ToolDefinition[6]
  -> tools/call(name, arguments)
       -> MCP adapter
       -> canonical ToolRegistry.call
       -> restricted DockerSandbox.invoke
       -> ToolResult
       -> MCP structured result + trace events
```

#### Security implications

- Stdio creates no listening socket and needs no remote authentication model.
- The MCP client receives only staged-workspace-relative paths and bounded outputs.
- All argument validation and execution still flow through the registry and container-side policy.
- The adapter cannot accept environment variables, command strings, Docker flags, base URLs, or host paths as tool arguments.
- One server process owns one run-scoped workspace; no cross-client or cross-run state is shared.
- Remote HTTP MCP transport, broad filesystem MCP servers, and third-party MCP tools are non-goals for V2.

### C. Acceptance criteria

- An MCP conformance test completes initialize, `tools/list`, and `tools/call` using the selected official SDK version.
- `tools/list` exposes exactly the same six names, descriptions, required fields, types, and additional-property rules as the direct provider schema path.
- Contract tests call every tool through direct and MCP adapters against equivalent staged fixtures and compare normalized result, error, and revision semantics.
- Unknown tools, malformed JSON, missing/extra/wrong-type arguments, path traversal, test edits, and non-allowlisted commands fail without sandbox-policy bypass or server crash.
- `_init_repo` and lifecycle methods never appear in MCP discovery.
- No host path or fake credential appears in MCP results or traces.
- On a fake in-process backend, median adapter overhead for 100 sequential calls is no more than 10 ms per call; sandbox/model time is excluded.
- The unchanged deterministic 12-case suite passes on the direct path. A one-case MCP-backed controller smoke test produces the same final diff and test result.

Successful implementation means MCP is a proven alternate transport over RepoPilot’s real controls, not a duplicate or less-restricted tool implementation.

### D. Evaluation plan

#### Metrics

- schema parity failures;
- normalized result parity by tool;
- adapter latency and serialization bytes;
- MCP protocol/validation error rate;
- revision divergence count;
- task/public/hidden/localization parity on a smoke subset;
- policy-bypass test count (must remain zero).

#### Baseline versus V2

The direct registry is the behavioral baseline. Compare direct versus MCP on deterministic tool contract cases and at least one full controlled task. MCP is successful if it adds interoperability with no result/security regression; it is not expected to improve task success.

#### Reused cases and new fixtures

Reuse tool-validation, patch-policy, Docker integration, and controlled benchmark fixtures. Add protocol lifecycle, cancellation/EOF, invalid-message, schema-parity, and direct-versus-MCP fixtures.

### E. Risks / trade-offs

- An MCP SDK adds dependency and version-compatibility work.
- Schema conversion can subtly change defaults or strictness.
- A long-lived stdio session retains mutable workspace state; revision semantics and single-session ownership must be explicit.
- MCP compatibility does not imply compatibility with every client implementation or safe remote deployment.
- Forcing internal runs through MCP too early would add latency and debugging layers without user value.

### F. CV/interview value

Demonstrates protocol adaptation, schema-driven tools, stateful tool-session design, contract testing, and security-boundary preservation. A candidate should be able to explain MCP lifecycle, why stdio was chosen, how direct/MCP parity was proven, why the registry remains authoritative, and why MCP was not used to broaden permissions.

## 9. P0.4 — Failure injection and explicit recovery evaluation

### A. Motivation

#### V1 limitation

V1 can recover when a model sees a tool error and chooses a better next action, as shown by a saved rejected-patch live run. However, provider exceptions terminate immediately, recovery rules are not explicit, failed-test repair is the only counted recovery cycle, and there is no repeatable suite for timeout, malformed action, response loss, transient error, or revision reconciliation.

#### General agent-engineering problem

Reliable agents need bounded behavior under partial failure. They must know which operations are safe to retry, preserve state after ambiguous mutations, stay within deadlines, and fail closed on policy violations. Deterministic fault injection is the appropriate way to test these properties without waiting for random provider or container failures.

### B. Proposed architecture

#### Existing components touched

- `agent/loop.py`, `models.py`, and `config.py`
- provider adapters and `ModelClient` error contract
- canonical `ToolBackend`/`ToolRegistry`
- trace schema, taxonomy, metrics, reports, and CLI

#### New modules/interfaces

- `agent/recovery.py`: immutable `RecoveryPolicy` and decision records.
- `evaluation/faults.py`: validated fault scenario definitions and model/tool decorators.
- `evaluation/reliability.py`: deterministic scenario runner and report generator.
- `configs/faults/*.json`: named, reviewable schedules keyed by boundary, occurrence, and typed fault.

Faults are injected **outside** sandbox policy and **inside** run instrumentation:

- model boundary: transient exception, permanent exception, malformed action, empty response;
- registry boundary: validation error, synthetic transport timeout, response loss before/after known execution;
- tool outcome: patch rejection, read timeout, test failure, test timeout;
- controller boundary: near-deadline condition or exhausted iteration/repair budget.

Fault scenarios never change paths, commands, mounts, capabilities, network settings, or test protection. They return typed synthetic outcomes or wrap fakes; they do not simulate failure by disabling controls.

Recovery decisions are explicit and conservative:

| Failure | Default policy |
|---|---|
| Retryable provider error before a turn is produced | Retry within a separate small model-retry budget and total deadline |
| Read-only tool transport failure known to occur before execution | At most one automatic retry |
| `run_tests` timeout | Record and return to the agent; an optional profile may allow one rerun |
| Invalid arguments or rejected patch | Do not auto-retry; return structured evidence to the model |
| `apply_patch` response lost after possible execution | Never blindly replay; reconcile with `git_diff` and revision before deciding |
| Policy rejection | Never retry automatically and never weaken policy |
| Non-retryable provider/sandbox error | Stop with explicit failure classification |

Model retries are counted separately from agent iterations so a provider transport blip does not silently consume the reasoning budget. All retries still consume the total wall-clock deadline.

#### Data/control flow

1. Reliability runner loads a canonical evaluation profile plus a fault schedule.
2. Fault decorators wrap `ModelClient` and/or `ToolBackend` for the selected occurrence.
3. The normal single-agent controller runs unchanged actions except for the injected outcome.
4. `RecoveryPolicy` records a decision with evidence, retry budget, idempotency class, and deadline state.
5. Trace/taxonomy record injected fault, recovery attempt, reconciliation, and final outcome.
6. The reliability report compares faulted runs to unmodified deterministic baselines.

#### Security implications

- Fault configuration is declarative and cannot name Python callables or commands.
- Injection is disabled by default in `run` and live `eval`; reports visibly mark `fault_injection: true`.
- Mutating tools are not blindly retried after ambiguous outcomes.
- Policy failures are test signals, never candidates for fallback execution.
- Total deadlines, observation limits, patch limits, and repair limits remain active under retry.

### C. Acceptance criteria

The initial reliability matrix must include at least these scenarios:

1. one retryable model error followed by normal recovery;
2. repeated/non-retryable model error followed by bounded stop;
3. invalid tool arguments followed by a corrected action;
4. patch-context rejection followed by a corrected patch;
5. read-only tool pre-execution timeout followed by one safe retry;
6. ambiguous `apply_patch` response followed by diff/revision reconciliation and no duplicate edit;
7. public-test timeout followed by bounded agent/controller behavior;
8. deadline exhaustion during retries followed by `total_timeout`.

Required outcomes:

- all scenarios declared recoverable finish with their expected public/hidden result;
- all declared non-recoverable scenarios stop with the expected taxonomy label and no extra mutation;
- no scenario exceeds configured retry, repair, iteration, or total-time budgets;
- workspace revision remains monotonic and reconciles with changed files;
- the same seed/schedule produces the same injected event sequence and classification;
- unfaulted deterministic runs remain unchanged;
- reliability reports separate injected failures from naturally occurring failures.

Successful implementation means recovery behavior is deliberate, bounded, state-safe, and reproducible—not merely that the model happened to try again.

### D. Evaluation plan

#### Metrics

- recovery success rate by injected fault type;
- attempts and recovery latency;
- task/public/hidden success after recovery;
- duplicate-mutation count;
- state/revision divergence count;
- budget overshoot count;
- unsafe retry count;
- final failure-label accuracy;
- additional tool calls/tokens versus unfaulted baseline.

#### Baseline versus V2

For each selected controlled case, run the canonical scripted trajectory with no fault and with one predeclared fault. Compare outcome, diff, revisions, calls, latency, and labels. Live reliability experiments are optional after deterministic correctness and should use low-cost repeats; fault injection is primarily infrastructure/recovery evidence, not a model leaderboard.

#### Reused cases and new fixtures

Reuse all 12 controlled cases, especially the previously observed patch-rejection pattern. Most fault tests need no new repository. Add fake provider/backend fixtures and declarative schedules. Add one fixture with a multi-file patch if needed to test ambiguous mutation reconciliation.

### E. Risks / trade-offs

- Retry logic can mask persistent defects or inflate cost.
- Simulated faults may not reproduce every SDK, kernel, or Docker failure.
- Ambiguous mutation recovery is substantially harder than read-only retry.
- Automatic retry metrics can be gamed by permissive policies; unsafe retries and budget cost must be reported.
- “Reliability tested” must be scoped to the enumerated scenarios, not generalized to production availability.

### F. CV/interview value

Demonstrates fault modeling, idempotency reasoning, retry budgets, deadlines, mutation reconciliation, deterministic chaos-style testing, and reliability metrics. A candidate should be able to explain why reads and writes have different retry policies, how an ambiguous patch result is reconciled, how recovery cost is measured, and why injected success does not prove production reliability.

## 10. P1.1 — Hybrid structural, lexical, and semantic repository context

### A. Motivation

#### V1 limitation

The V1 AST map is deterministic and effective when issue terms overlap paths/symbols or import neighbors. It cannot match paraphrases such as “undo a partially completed allocation” to a symbol named `rollback_reservations` when filenames, signatures, and issue words do not overlap. Literal/regex search helps only when the model already has a likely term.

#### General agent-engineering problem

Repository agents must select useful context under strict token and latency budgets. Structure captures code organization, lexical ranking captures exact identifiers, and embeddings capture paraphrase, but each produces different errors. The engineering problem is combining them reproducibly without leaking code, adding a database, or hiding cost.

### B. Proposed architecture

#### Existing components touched

- `sandbox/repository_context.py` as a backward-compatible façade
- `sandbox/sandbox_runner.py` for `list_files`
- `sandbox/docker.py` and `Dockerfile`
- `config.py`, run metadata, prompt text, trace, and evaluation metrics
- repository-context tests

#### New modules/interfaces

- `retrieval/base.py`: `ContextSelector`, `ContextCandidate`, and `RetrievalResult` contracts.
- `retrieval/chunking.py`: deterministic AST-aware chunks for modules, classes, functions/methods, and bounded non-Python text.
- `retrieval/structural.py`: current AST/issue/import-neighbor behavior factored without changing its baseline ordering.
- `retrieval/lexical.py`: explicit bounded term/BM25-like scorer over candidate metadata and text.
- `retrieval/semantic.py`: optional local embedding backend.
- `retrieval/hybrid.py`: deterministic rank fusion and budget selection.

`list_files` remains the tool. Its `repository_context` result evolves to include:

- selected formatted map/content within the existing character cap;
- ordered candidate records with path, symbol/range, role, component ranks, fused rank, and selection reason;
- retrieval strategy and version;
- indexing/chunking/selection latency;
- truncation and cache status.

The current AST map becomes the `structural` strategy and remains the default until the comparison gate passes. `semantic` uses a pinned compact embedding model executed **inside the no-network container**. Model weights are baked into a versioned optional sandbox image; repository text is never sent to a remote embedding API. The embedding dependency should be an optional extra/image variant, not required for V1/direct structural runs.

Candidates are deterministic chunks keyed by content hash. Because each tool call launches a new sandbox process, index metadata and vectors are cached in a bounded, non-executable container tmpfs directory, never in the mounted Git worktree. Cache entries are keyed by repository file hashes, chunker version, model hash, and retrieval configuration. Changed files invalidate only affected chunks. If cache/resource limits are exceeded, the semantic component fails explicitly or falls back according to the profile; fallback is traced and never silently reported as hybrid.

Hybrid ranking uses rank-based fusion (for example reciprocal-rank fusion) rather than adding incomparable raw AST, lexical, and cosine scores. Exact weights/constants are fixed in the evaluation profile and tuned only on a development retrieval set, not on the held-out acceptance set.

#### Data/control flow

1. `list_files` enumerates bounded safe files as today.
2. Chunker parses eligible files as untrusted text/AST within byte/file/chunk limits.
3. Structural and lexical selectors rank candidates.
4. Optional local embedder ranks the same candidates against the issue.
5. Hybrid fusion combines ranks, applies source/test role and import-neighbor policy, then packs candidates into file/symbol/character budgets.
6. The selected context and component ranks return through the existing tool result.
7. Trace analytics record candidate hit metrics only during evaluation, using benchmark oracles unavailable to the agent.

#### Security implications

- Embedding executes inside the existing networkless, non-root, read-only-root container.
- No remote embedding service receives repository content.
- Weights are pinned and verified at image build time, not downloaded during a run.
- Cache is bounded, ephemeral, `noexec`, outside the Git worktree, and removed with the container.
- Existing path, file-size, scan-count, output-size, and symlink controls remain.
- Retrieved repository text remains untrusted prompt content and cannot override the system prompt.

### C. Acceptance criteria

- Structural mode reproduces current repository-context outputs or an explicitly versioned equivalent and passes all existing context/security tests.
- All strategies obey the same maximum scan files, chunk count, file bytes, output characters, and selected-symbol/file budgets.
- Repeated runs with the same image/model/config/repository return identical candidate ordering and selected context.
- No repository content leaves the container in a remote embedding request; a network-attempt test fails because container networking remains disabled.
- Cache files never appear in `git_diff` or the host input repository.
- On a predeclared held-out retrieval-challenge set, hybrid expected-fix-file Recall@5 is at least 0.85, is no worse than the best single strategy, and semantic ranking uniquely recovers at least two paraphrase cases missed by structural ranking.
- On the existing 12 cases, structural and hybrid expected-fix-file Recall@5 remain 1.00.
- On the existing 252-file stress fixture, context remains within configured limits; a warm hybrid selection completes within a predeclared local-hardware budget and reports cold/warm latency separately. The first implementation target is <=3 seconds cold and <=500 ms warm on the documented development machine, not a production SLA.
- If semantic initialization fails, the report identifies the fallback strategy and error; it never labels structural-only output as hybrid.

Successful implementation means hybrid retrieval wins a held-out localization comparison under explicit cost and security constraints, not merely that embeddings are present.

### D. Evaluation plan

#### Metrics

- expected-file Recall@1/@3/@5 and mean reciprocal rank;
- expected-symbol/range hit rate where annotated;
- context precision (selected relevant candidates / selected candidates);
- selected context characters and chunks;
- cold index, warm query, chunking, and fusion latency;
- cache hit rate and index size;
- retrieval-related tool calls, total tokens, and end-to-end task success;
- strategy fallback/error rate.

#### Baseline versus V2

Use three fixed strategies: `structural` (V1-equivalent), `semantic`, and `hybrid`. Run an offline retrieval comparison first because it is deterministic and isolates localization from model behavior. Then run paired live-agent comparisons with the same provider/model, prompt, controller budgets, case ordering policy, and repetitions. Deterministic scripted task success is retained only as infrastructure validation; it is not counted as retrieval evidence.

#### Reused cases and new fixtures

Reuse the 12 controlled cases for regression and the 252-file stress construction for budget/determinism. Add 8–12 retrieval-challenge cases with:

- issue/code paraphrases and intentionally different identifiers;
- plausible lexical distractors;
- relevant import neighbors whose own names do not match the issue;
- cross-file behavior and `src/` layouts;
- at least two cases where exact lexical evidence remains superior to embeddings;
- held-out expected file and, where defensible, expected symbol/range metadata.

These fixtures must preserve public/hidden-test discipline and must not expose oracle fields to the agent.

### E. Risks / trade-offs

- Embedding dependencies and weights materially increase image size and build time.
- CPU embedding increases cold-start latency and may consume context-cache memory.
- Semantic similarity can prefer conceptually related distractors or prompt-injected comments.
- Rank-fusion tuning can overfit a small benchmark.
- A compact model may be weak on code or non-English issues; claims must name the model and corpus.
- A remote embedding API would be simpler but would weaken the current privacy/boundary story and is therefore excluded.
- No vector database is needed for run-scoped repositories and current size limits.

### F. CV/interview value

Demonstrates code chunking, hybrid retrieval, local embedding inference, rank fusion, cache invalidation, retrieval evaluation, and security/cost trade-offs. A candidate should be able to explain why structural and semantic signals are complementary, why ranks are fused instead of raw scores, how leakage is prevented, how retrieval is evaluated independently from the agent, and why an ephemeral cache is sufficient.

## 11. P1.2 — Controlled retrieval baseline comparison

This is specified separately because a retrieval implementation without a valid comparison would be résumé-driven complexity rather than evidence.

### A. Motivation

#### V1 limitation

The current benchmark reports changed-file localization after an agent run but does not measure whether the initial context selector ranked the correct file. The scripted model already knows where to read and what patch to apply, so 12/12 deterministic success cannot distinguish structural, semantic, or hybrid retrieval.

#### General agent-engineering problem

Agent components must be evaluated both in isolation and end to end. Otherwise model variance, prompt behavior, or benchmark leakage can be mistaken for retrieval quality.

### B. Proposed architecture

#### Existing components touched

- benchmark case metadata and loader
- evaluation runner/metrics/reports
- CLI and retrieval trace events

#### New modules/interfaces

- `evaluation/retrieval.py`: offline evaluator and paired comparison runner.
- `benchmarks/retrieval/`: development and held-out challenge fixtures, kept separate from the historical 12-case corpus.
- `configs/evaluation/retrieval_comparison.json`: predeclared strategies, budgets, metrics, thresholds, provider/repetition policy, and randomization seed.

The offline evaluator invokes context selection directly inside the sandbox and compares ranked candidates with hidden oracle metadata. The paired live evaluator runs identical tasks under each strategy. Reports present each strategy individually and paired deltas; they do not collapse deterministic and live results.

#### Data/control flow

1. Freeze development/held-out split and evaluation profile.
2. Validate fixtures and confirm oracle data is outside the staged repository/model prompt.
3. Run structural, semantic, and hybrid offline selection.
4. Apply acceptance thresholds before live spending.
5. Run fixed live repetitions in randomized/block-balanced strategy order.
6. Report paired task/retrieval/cost deltas and raw per-run artifacts.

#### Security implications

Same as P1.1. Evaluation oracles and solution patches remain outside the agent workspace. Reports must not include embedding vectors or unbounded source chunks.

### C. Acceptance criteria

- The comparison profile is committed before held-out results are inspected.
- Fixture validation proves oracle files exist, buggy public/hidden behavior is reproduced, reference patches pass, and oracle fields are absent from the staged agent workspace.
- Offline reports include all three strategies, identical budgets, per-case ranks, and paired aggregate metrics.
- Live reports use the same provider/model/version, controller limits, prompt, and case set for all strategies; missing or failed runs are retained rather than discarded.
- At least three live repetitions per case/strategy are used for any claim about success-rate or tool/token improvement, budget permitting. A one-pass run is labeled exploratory.
- Hybrid is promoted to default only if it meets P1.1 retrieval thresholds, does not reduce controlled live task success, and reports its latency/token cost. If it fails, structural remains default and the negative result is documented.

### D. Evaluation plan

#### Primary metrics

Offline primary: expected-file Recall@5 and MRR. Live primary: hidden-test task success. Secondary: tool calls before first read of an expected file, total tokens, total latency, unnecessary calls, and changed-file F1.

#### Baseline versus V2

Structural is the locked V1-equivalent baseline. Semantic and hybrid are compared by paired case, not against historical runs from a different model version or date.

#### Reused cases and new fixtures

Use current cases only for regression/non-inferiority. Use the new held-out retrieval suite for discrimination. Saved historical live results may provide context but are not a valid controlled baseline.

### E. Risks / trade-offs

- Multiple live repetitions cost money and remain model-variable.
- Small challenge sets produce wide uncertainty; publish raw counts and intervals, not sweeping generalizations.
- Fixture authors can unintentionally favor embeddings or lexical matching.
- End-to-end gains may be absent even when offline ranking improves because the model already localizes effectively.

### F. CV/interview value

Demonstrates experimental design, component versus end-to-end evaluation, paired baselines, leakage controls, threshold precommitment, and honest negative-result handling. A candidate should be able to explain why scripted evaluation cannot validate retrieval and how model/version/order effects were controlled.

## 12. P2 — Optional SWE-bench Verified behavioral evaluation

### A. Motivation

#### V1 limitation

RepoPilot validates five genuine SWE-bench Verified task definitions, immutable revisions, reference/test patches, and compile integrity, but does not run the live agent or grade generated patches behaviorally. This is correctly labeled reference integrity.

#### General agent-engineering problem

Synthetic tasks give control and diagnostic clarity but limited external validity. A small real-world behavioral track tests repository scale, issue ambiguity, dependency environments, and standardized FAIL_TO_PASS/PASS_TO_PASS grading.

### B. Proposed architecture

#### Entry gate: environment feasibility spike

Do not implement the full feature until a spike proves that selected official SWE-bench per-instance images can run RepoPilot's agent tools with:

- agent execution network disabled;
- non-root user;
- read-only container root;
- dropped capabilities and `no-new-privileges`;
- bounded memory/CPU/PIDs/time;
- only a staged writable worktree mounted;
- no host credentials, home directory, Docker socket, or API keys;
- controller-selected fixed test plans that the model cannot modify.

If this cannot be achieved for a task, exclude it with a documented compatibility reason. Do not weaken the V1 sandbox or call prediction-only patch generation “behavioral evaluation.”

#### Existing components touched

- `evaluation/real_world.py` without changing `real-validate` semantics
- `sandbox/docker.py`, `policy.py`, and configuration through a new backend abstraction only if the spike passes
- `agent/loop.py` lifecycle reuse
- CLI, reports, and real-world corpus tests

#### New modules/interfaces

- `evaluation/swebench.py`: live prediction runner and official-harness result importer.
- `sandbox/test_plan.py`: trusted, immutable per-instance test plans distinct from model tool arguments.
- optionally `sandbox/swebench.py`: hardened derived-image adapter implementing the same six tool behaviors.
- `configs/evaluation/swebench_verified_small.json`: frozen instance IDs, model settings, limits, and grading policy.

Keep three separate commands/artifacts:

1. `real-validate`: existing checkout/reference integrity, unchanged.
2. `real-eval`: live agent produces one official-format prediction per selected instance from base revision only.
3. `real-grade` or an import step: official SWE-bench harness grades predictions and RepoPilot imports FAIL_TO_PASS/PASS_TO_PASS results.

The agent must never see `reference.patch`, `test.patch`, expected-fix metadata, or gold predictions. Acquisition/image setup may require network outside agent execution; agent tool execution remains networkless. Gold-patch harness validation should run first as an environment sanity check and be reported separately from live predictions.

Start with three predeclared instances from the existing five based on environment reproducibility and security compatibility, not after seeing live model success. Expand to all five only after the initial pipeline is stable.

#### Data/control flow

1. Validate metadata and exact base commit using the existing reference-integrity path.
2. Acquire/build the official instance environment outside the agent run.
3. Stage only the base repository into a hardened, networkless run workspace.
4. Run the normal single agent and six tools with a trusted fixed test plan.
5. Export the agent’s final diff as official prediction JSONL.
6. Grade with the official SWE-bench harness.
7. Import per-instance FAIL_TO_PASS, PASS_TO_PASS, harness error, latency, tokens, calls, and taxonomy artifacts.
8. Keep reference-integrity and behavioral reports in different directories and tracks.

#### Security implications

- Official images are untrusted until inspected and wrapped; no task is admitted merely because the official harness can run it.
- Any derived image must meet the same container-inspection assertions as the V1 integration test.
- Trusted test plans are versioned controller data, not model input.
- Setup network and agent-execution network are reported separately.
- Reference and test patches stay outside the staged workspace.
- The official harness may have its own privileges when orchestrating grading; its boundary and RepoPilot's agent sandbox must be documented separately.

### C. Acceptance criteria

- The environment spike passes every V1 sandbox security assertion for each admitted instance.
- Gold reference predictions pass official FAIL_TO_PASS/PASS_TO_PASS grading for every admitted instance before live agent evaluation.
- A contamination test proves gold/reference/test patches and oracle metadata are absent from agent prompts, tool outputs, workspace, and trace.
- Live output is valid official-format JSONL with exact instance IDs and base commits.
- The official harness, version/image identifiers, command, and raw result artifacts are recorded.
- Harness/setup failure is classified separately and does not become agent failure.
- Reports show numerator/denominator, not only percentage, and never claim more tasks than officially graded.
- A behavioral success claim requires both FAIL_TO_PASS and PASS_TO_PASS criteria from the official harness.
- `real-validate` remains unchanged and still reports only reference integrity.

Successful implementation means RepoPilot can make a narrowly scoped, reproducible claim such as “the pinned agent configuration resolved X of 3 officially graded instances,” while retaining all failures and costs.

### D. Evaluation plan

#### Metrics

- official resolved rate and raw count;
- FAIL_TO_PASS and PASS_TO_PASS pass counts;
- harness/setup/agent failure counts;
- changed-file localization against metadata, reported as diagnostic only;
- tool calls, retries, tokens, wall time, and stop reasons;
- retrieval strategy metrics if P1 is enabled;
- contamination/security-gate pass rate.

#### Baseline versus V2

The reference patch is an environment sanity baseline, not an agent baseline. Compare structural and hybrid only if P1’s controlled gate has passed and budgets allow paired runs. Do not compare a current model run to published SWE-bench leaderboard scores without matching harness/version/settings.

#### Reused cases and new fixtures

Reuse the existing five task definitions and reference patches. No new SWE-bench instances are needed initially. New environment manifests, official result fixtures, contamination tests, and hardened-image inspection tests are required.

### E. Risks / trade-offs

- Official environments are large, slow, and version-sensitive.
- Security hardening may be incompatible with some upstream tests.
- Three to five tasks are illustrative, not statistically representative.
- Real-world issues may exceed current iteration/token limits or the six-tool UX.
- Harness orchestration can be mistaken for RepoPilot production deployment; avoid that claim.
- Selecting tasks after seeing results creates bias; freeze the subset first.
- This work can consume more time than all P0 modules combined, which is why it remains P2.

### F. CV/interview value

Demonstrates external benchmark integration, immutable dataset handling, environment reproducibility, leakage prevention, standardized behavioral grading, and careful claim boundaries. A candidate should explain the distinction among gold/reference integrity, agent prediction generation, and official behavioral grading; why security compatibility is a gate; and why a small subset is not a leaderboard result.

## 13. Cross-cutting evaluation rules

### 13.1 Track names

Every report must declare exactly one primary track:

- `controlled_deterministic_infrastructure`
- `controlled_live_agent`
- `controlled_reliability`
- `retrieval_offline`
- `retrieval_live_agent`
- `swebench_reference_integrity`
- `swebench_live_behavioral`

Aggregate dashboards are not planned. A comparison report may link tracks but may not merge their success numerators.

### 13.2 Reproducibility fields

Every V2 report records:

- RepoPilot commit and dirty-worktree flag;
- profile ID/version/hash;
- benchmark corpus version/hash and case IDs;
- provider/model metadata and repetition number;
- sandbox image digest and security configuration;
- retrieval strategy/model/chunker/fusion versions;
- taxonomy version;
- fault scenario and seed, if any;
- start/end timestamps and host platform summary;
- official harness/image versions for SWE-bench behavioral runs.

Secrets and complete environment dumps are forbidden.

### 13.3 Statistical reporting

- Deterministic tracks report exact counts and assert repeatability.
- Live tracks retain every attempt and report raw counts, medians/distributions, and paired deltas where applicable.
- At least three repetitions are required before describing a live difference as a trend; otherwise label it a smoke or exploratory run.
- Do not imply statistical significance from 12 controlled or 3–5 real-world tasks.
- Cost is calculated only from provider-reported usage plus a dated, cited price table; otherwise report tokens without currency.

## 14. Phased implementation plan

### Phase 0 — Freeze V1 compatibility baselines

- Add no behavior.
- Record current test results, deterministic 12-case report, saved live report references, trajectory schema examples, and Docker security assertions.
- Define golden direct-tool schemas/results for the six tools.
- Mark existing user changes as out of scope and preserve them.

Exit criterion: reproducible V1 baseline artifacts and golden contract fixtures exist.

### Phase 1 — Trace schema and analytics

- Add V2 event envelope, reader, compatibility normalizer, validation, redaction, and analytics.
- Instrument model/tool/controller/evaluation spans.
- Reconcile trace-derived metrics with `run.json` and existing reports.
- Add `trace-summary` CLI.

Exit criterion: P0.1 acceptance criteria pass on unit tests and all 12 deterministic cases.

### Phase 2 — Evaluation profiles and taxonomy

- Add canonical JSON profiles and versioned taxonomy.
- Normalize provider/tool/controller errors.
- Classify saved/current traces and extend reports.
- Keep deterministic/live/reference-integrity outputs separate.

Exit criterion: P0.2 label coverage and gold-fixture criteria pass.

### Phase 3 — Canonical tool interface and MCP stdio

- Extract canonical tool definitions and `ToolBackend` protocol.
- Introduce one-run session lifecycle without changing sandbox behavior.
- Add MCP stdio adapter, protocol tests, schema/result parity tests, and one controlled smoke task.

Exit criterion: P0.3 conformance, parity, security, and overhead criteria pass.

### Phase 4 — Reliability policies and fault suite

- Add normalized retryability, recovery budgets, mutation reconciliation, declarative fault schedules, and reliability runner.
- Execute the eight-scenario matrix on selected controlled cases, then expand across the corpus where useful.

Exit criterion: P0.4 recoverable/non-recoverable outcome matrix passes with zero unsafe retries or revision divergence.

### Phase 5 — Retrieval experiment scaffolding

- Add ranked candidate records to the structural selector without changing its order.
- Build retrieval challenge fixtures and freeze development/held-out split and evaluation profile.
- Implement offline structural baseline and leakage checks.

Exit criterion: baseline metrics are reproducible and the benchmark can discriminate plausible selectors.

### Phase 6 — Local semantic and hybrid retrieval

- Add chunking, pinned local embedder, ephemeral bounded cache, and rank fusion.
- Run offline comparison; promote to live comparison only after offline thresholds pass.
- Keep structural default unless the full promotion gate passes.

Exit criterion: P1.1/P1.2 security, determinism, retrieval, cost, and non-inferiority criteria pass.

### Phase 7 — SWE-bench behavioral feasibility and optional pilot

- Run the security/environment feasibility spike on the frozen subset.
- Stop and document exclusions if security parity is impossible.
- If feasible, add live prediction generation, official grading import, contamination tests, and three-instance pilot.

Exit criterion: P2 acceptance criteria pass. Otherwise retain only the current reference-integrity track and document why behavioral support was not shipped.

## 15. Exact files/modules likely to be added or modified

This is a planning map, not permission to implement all files at once. Names may adjust slightly during implementation, but responsibilities should not migrate across boundaries without updating this spec.

### 15.1 Existing files likely to be modified

| File | Intended V2 change |
|---|---|
| `src/repopilot/models.py` | structured errors, retry/recovery fields, trace-derived result fields |
| `src/repopilot/config.py` | evaluation/retrieval/recovery configuration types |
| `src/repopilot/cli.py` | `trace-summary`, `mcp-serve`, `reliability-eval`, `retrieval-eval`, optional `real-eval`/grading import |
| `src/repopilot/agent/loop.py` | trace spans, `ToolBackend` typing, bounded recovery decisions; preserve single-agent control |
| `src/repopilot/tools/contracts.py` | evolve into or import canonical `ToolDefinition` catalog |
| `src/repopilot/tools/registry.py` | transport-neutral backend interface, structured errors, idempotency/mutation metadata |
| `src/repopilot/tools/__init__.py` | export canonical interfaces |
| `src/repopilot/trajectory/recorder.py` | V2 envelope, IDs, redaction, span helpers |
| `src/repopilot/trajectory/__init__.py` | export readers/analytics/types |
| `src/repopilot/sandbox/docker.py` | run session hooks, bounded retrieval cache mount, optional hardened image metadata |
| `src/repopilot/sandbox/sandbox_runner.py` | richer structured results and retrieval selector integration; no broader commands |
| `src/repopilot/sandbox/repository_context.py` | compatibility façade over structural selector |
| `src/repopilot/sandbox/policy.py` | mutability/test-plan classifications; preserve current allowlist for normal runs |
| `src/repopilot/llm/base.py` | normalized model error contract |
| `src/repopilot/llm/openai_adapter.py` | typed provider errors and trace metadata |
| `src/repopilot/llm/deepseek_adapter.py` | typed provider errors and trace metadata |
| `src/repopilot/evaluation/cases.py` | optional retrieval oracle metadata kept out of agent inputs |
| `src/repopilot/evaluation/runner.py` | profiles, trace-derived metrics, taxonomy integration |
| `src/repopilot/evaluation/metrics.py` | distributions, recovery/failure/retrieval metrics |
| `src/repopilot/evaluation/reports.py` | track-specific sections and evidence links |
| `src/repopilot/evaluation/real_world.py` | reuse definitions; keep `real-validate` behavior unchanged |
| `Dockerfile` | optional pinned MCP/retrieval runtime assets; keep security runtime behavior |
| `pyproject.toml` | pinned MCP and local-embedding optional dependencies only when implemented |

`README.md` is intentionally not included in the initial implementation phases and must not be edited until measured V2 results exist.

### 15.2 New source modules likely to be added

```text
src/repopilot/session.py
src/repopilot/agent/recovery.py
src/repopilot/tools/backend.py
src/repopilot/tools/definitions.py              # or keep this responsibility in contracts.py
src/repopilot/mcp/__init__.py
src/repopilot/mcp/adapter.py
src/repopilot/mcp/server.py
src/repopilot/trajectory/schema.py
src/repopilot/trajectory/reader.py
src/repopilot/trajectory/analytics.py
src/repopilot/evaluation/profiles.py
src/repopilot/evaluation/taxonomy.py
src/repopilot/evaluation/faults.py
src/repopilot/evaluation/reliability.py
src/repopilot/evaluation/retrieval.py
src/repopilot/retrieval/__init__.py
src/repopilot/retrieval/base.py
src/repopilot/retrieval/chunking.py
src/repopilot/retrieval/structural.py
src/repopilot/retrieval/lexical.py
src/repopilot/retrieval/semantic.py
src/repopilot/retrieval/hybrid.py
src/repopilot/sandbox/test_plan.py               # P2 only
src/repopilot/sandbox/swebench.py                # P2 only, only after feasibility gate
src/repopilot/evaluation/swebench.py             # P2 only
```

### 15.3 New configuration/benchmark artifacts likely to be added

```text
configs/evaluation/default.json
configs/evaluation/retrieval_comparison.json
configs/evaluation/swebench_verified_small.json  # P2 only
configs/evaluation/failure_taxonomy.v1.json
configs/faults/*.json
benchmarks/retrieval/dev/*
benchmarks/retrieval/held_out/*
```

### 15.4 New tests likely to be added

```text
tests/unit/test_trace_schema.py
tests/unit/test_trace_reader.py
tests/unit/test_trace_analytics.py
tests/unit/test_trace_redaction.py
tests/unit/test_evaluation_profiles.py
tests/unit/test_failure_taxonomy.py
tests/unit/test_tool_definitions.py
tests/unit/test_mcp_adapter.py
tests/unit/test_recovery_policy.py
tests/unit/test_fault_injection.py
tests/unit/test_retrieval_chunking.py
tests/unit/test_semantic_retrieval.py
tests/unit/test_hybrid_retrieval.py
tests/integration/test_mcp_stdio.py
tests/integration/test_mcp_sandbox_security.py
tests/integration/test_retrieval_sandbox.py
tests/regression/test_trace_metric_reconciliation.py
tests/regression/test_reliability_matrix.py
tests/regression/test_retrieval_comparison.py
tests/regression/test_swebench_behavioral_pipeline.py  # P2, mostly fixture/import tests
```

Existing tests should be extended rather than cloned when they already own the relevant invariant, especially Docker security, provider credentials, tool arguments, patch policy, repository context, CLI, benchmark corpus, and real-world corpus integrity.

## 16. Dependency graph between tasks

```text
V1 baseline freeze
  |
  +--> Trace schema + reader + redaction
  |      |
  |      +--> Trace analytics + metric reconciliation
  |      |      |
  |      |      +--> Evaluation profiles + failure taxonomy
  |      |             |
  |      |             +--> Fault injection + recovery reports
  |      |             |
  |      |             +--> Retrieval comparison reports
  |      |             |
  |      |             +--> SWE-bench behavioral reports
  |      |
  |      +--> Structured tool/model error envelopes
  |             |
  |             +--> Canonical tool definitions + ToolBackend
  |                    |
  |                    +--> MCP stdio adapter
  |                    |
  |                    +--> Fault-injecting tool decorator
  |
  +--> Structural ranked-candidate baseline
         |
         +--> Retrieval challenge corpus + offline evaluator
         |      |
         |      +--> Local semantic backend + cache
         |             |
         |             +--> Hybrid fusion
         |                    |
         |                    +--> Paired live retrieval evaluation
         |
         +--> Optional SWE-bench feasibility spike
                |
                +-- security gate passes --> hardened instance backend
                                             |
                                             +--> live prediction + official grading
                +-- security gate fails  --> retain reference-integrity only
```

MCP and retrieval can be developed after the common trace/error contracts and mostly in parallel, but recovery evaluation should wait for taxonomy and tool idempotency classifications. SWE-bench behavioral work depends on stable traces/reports and benefits from P1 retrieval, but it must not block P0/P1 completion.

## 17. Recommended implementation order

1. Freeze golden V1 tool/trace/report/security baselines.
2. Implement V2 trace envelope, reader, redaction, and analytics.
3. Add evaluation profiles, normalized errors, and failure taxonomy.
4. Extract canonical tool definitions and `ToolBackend`; prove no direct-path regression.
5. Add the local stdio MCP adapter and parity/security tests.
6. Add recovery policies, then deterministic fault injection and the reliability matrix.
7. Instrument the structural selector with ranked candidates and build the retrieval benchmark before adding embeddings.
8. Add the local semantic backend/cache and hybrid rank fusion.
9. Run offline then paired live retrieval comparisons; promote hybrid only if its gate passes.
10. Run the SWE-bench environment/security feasibility spike.
11. Only if the spike passes, implement the three-instance behavioral pilot and official result import.
12. Update README/CV claims only after final measured reports exist.

The first implementation change should be the versioned trace/error foundation, not MCP. The first user-visible V2 feature may still be MCP, but it should be built on contracts whose behavior and failures are already measurable.

## 18. NON-GOALS

- Multi-agent planning, delegation, debate, or orchestration.
- Persistent memory, user profiles, cross-run learning, or long-term state.
- A database, vector database, data warehouse, message queue, or cache service.
- A web UI, dashboard, IDE extension, or hosted API.
- Kubernetes, distributed execution, autoscaling, service discovery, or production deployment.
- Remote/network MCP transport or arbitrary third-party MCP tool installation.
- Expanding beyond the six controlled repository tools without a separate security and evaluation case.
- Arbitrary shell access or model-selected test commands.
- Weakening Docker isolation, staging, path checks, resource limits, test protection, or credential separation.
- Sending repository code to a remote embedding provider.
- Replacing the existing AST map with embeddings; semantic retrieval is additive and experimentally gated.
- A general-purpose code search engine or cross-repository index.
- Reproducing the full SWE-bench infrastructure matrix inside RepoPilot.
- Claiming SWE-bench success from reference-patch applicability, compile checks, or prediction generation without official behavioral grading.
- Claiming production readiness, production observability, production reliability, or production scale.
- Provider leaderboard claims based on unmatched model versions, prompts, dates, cases, or run counts.
- Refactoring unrelated implementation solely to create cleaner diagrams or additional modules.

## 19. V1 -> V2 capability table

| Capability | V1 | V2 target |
|---|---|---|
| Agent topology | One tool-calling coding agent | Same one agent; explicit recovery policy |
| Public tools | Six typed repository tools | Same six from one canonical catalog |
| Tool transport | Direct provider/registry calls | Direct plus local stdio MCP adapter with parity tests |
| Sandbox | Restricted, networkless Docker over staged copy | Same boundary; optional components run inside it |
| Repository context | Issue-ranked Python AST outline plus literal/regex search | Structural baseline plus optional local semantic and hybrid rank fusion behind `list_files` |
| Retrieval storage | Recomputed bounded outline | Ephemeral, bounded container cache; no database |
| Trajectory | Append-only schema-v1 JSONL | Versioned validated spans/events, V1 reader, redaction, local analytics |
| Errors | Free-form messages and stop reasons | Stable error envelopes, retryability, evidence-linked taxonomy |
| Recovery | Model reacts to tool errors; failed-test repair budget | Explicit retry/idempotency/reconciliation policy plus deterministic fault suite |
| Evaluation config | CLI/default driven | Named, hashed, versioned JSON profiles |
| Controlled evaluation | Deterministic and separate live runs over 12 hidden-test cases | Same tracks plus failure/recovery and paired retrieval experiments |
| Metrics | success, tests, localization, calls, repairs, latency, tokens, stop reason | V1 metrics plus distributions, trace reconciliation, failure/recovery and retrieval metrics |
| Failure analysis | Unnecessary calls and terminal stop reason | Multi-dimensional, configurable, evidence-based taxonomy |
| Real-world track | Five SWE-bench Verified reference-integrity checks | Integrity unchanged; optional security-gated live behavioral subset |
| Artifacts | JSONL, JSON, Markdown | Same file artifacts with richer schemas; no UI/database |
| Claims | Evaluated local coding-agent MVP | Local Agent Engineering & Evaluation Platform; still not production |

## 20. Definition of V2 completion

RepoPilot V2 is complete when all P0 modules pass their acceptance criteria, the original 12-case deterministic and sandbox-security baselines remain intact, and reports clearly demonstrate traceability, failure classification, MCP parity, and bounded recovery. P1 is complete only if the controlled retrieval comparison justifies hybrid retrieval; otherwise a well-documented structural default and negative experiment are acceptable. P2 is optional and is complete only with official behavioral grading under a sandbox-compatible environment. No README or portfolio claim should precede those measured artifacts.
