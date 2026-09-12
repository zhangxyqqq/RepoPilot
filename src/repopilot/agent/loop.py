from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from repopilot.agent.recovery import RecoveryPolicy, failure_from_exception
from repopilot.config import RunConfig
from repopilot.llm.base import ModelClient
from repopilot.models import RunResult, TokenUsage, ToolResult
from repopilot.session import RunSession
from repopilot.tools import ToolBackend
from repopilot.trajectory import (
    TrajectoryRecorder,
    reconcile_summary,
    redact_text,
    redact_value,
    summarize_trace,
)

#负责agent一轮轮怎么跑
class AgentLoop:
    def __init__(
        self,
        *,
        issue: str,
        model: ModelClient,
        tools: ToolBackend,
        recorder: TrajectoryRecorder,
        max_iterations: int,
        max_repair_cycles: int,
        total_timeout_seconds: int,
        recovery_policy: RecoveryPolicy | None = None,
        fault_runtime: Any | None = None,
        timing_hook: Callable[[str, float], None] | None = None,
    ):
        self.issue = issue
        self.model = model
        self.tools = tools
        self.recorder = recorder
        self.max_iterations = max_iterations
        self.max_repair_cycles = max_repair_cycles
        self.total_timeout_seconds = total_timeout_seconds
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self.fault_runtime = fault_runtime
        self.timing_hook = timing_hook

    def _timing(self, checkpoint: str, started: float) -> None:
        if self.timing_hook is not None:
            self.timing_hook(checkpoint, (time.perf_counter() - started) * 1000)

    def _record_tool(
        self,
        *,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        origin: str,
        revision_before_override: int | None = None,
    ) -> dict[str, Any]:
        revision_before = result.revision if revision_before_override is None else revision_before_override
        if name == "apply_patch" and result.ok and result.observation.get("changed"):
            revision_before = max(0, result.revision - 1)
        error: Any = result.error
        if result.failure is not None:
            error = asdict(result.failure)
        return self.recorder.record(
            "tool_call",
            iteration=iteration,
            origin=origin,
            injected=bool(result.failure and result.failure.injected) or bool(result.observation.get("injected")),
            fault_type=(
                result.failure.fault_type
                if result.failure is not None
                else result.observation.get("fault_type")
            ),
            tool=name,
            arguments=arguments,
            ok=result.ok,
            observation=result.observation,
            error=error,
            latency_ms=result.latency_ms,
            workspace_revision_before=revision_before,
            workspace_revision_after=result.revision,
        )

    def _remaining_deadline_ms(self, started: float) -> float:
        return (self.total_timeout_seconds - (time.perf_counter() - started)) * 1000

    def _record_decision(self, decision: Any, *, evidence_event_id: str, iteration: int) -> dict[str, Any]:
        return self.recorder.record(
            "recovery_decision",
            iteration=iteration,
            evidence_event_ids=[evidence_event_id],
            **asdict(decision),
        )

    def _record_outcome(
        self,
        decision_event: dict[str, Any],
        *,
        outcome: str,
        revision: int,
        success: bool,
        iteration: int,
    ) -> None:
        self.recorder.record(
            "recovery_outcome",
            iteration=iteration,
            decision_event_id=decision_event["event_id"],
            selected_action=decision_event["payload"]["action"],
            resulting_outcome=outcome,
            resulting_revision=revision,
            success=success,
        )

    def _controller_fault(self, checkpoint: str) -> str | None:
        if self.fault_runtime is None:
            return None
        fault = self.fault_runtime.take_controller(checkpoint)
        if fault is None:
            return None
        return {
            "near_deadline": "total_timeout",
            "iteration_budget_exhausted": "iteration_limit",
            "repair_budget_exhausted": "repair_limit",
            "retry_budget_exhausted": "retry_budget_exhausted",
        }[fault.fault_type]

    def run(self, run_id: str, *, started_at: float | None = None) -> RunResult:
        started = started_at if started_at is not None else time.perf_counter()
        self._timing("controller_started", started)
        history: list[dict[str, Any]] = []
        usage = TokenUsage()
        repair_cycles = 0
        tool_calls = 0
        iterations = 0
        final_message = ""
        stop_reason = "iteration_limit"
        last_test: dict[str, Any] | None = None
        last_test_revision: int | None = None
        first_passing_revision: int | None = None
        last_diff: dict[str, Any] | None = None
        last_diff_revision: int | None = None
        model_retries = 0
        read_retries: dict[tuple[str, str], int] = {}
        test_timeout_retries = 0
        forced_stop: str | None = None
        #最多让llm思考max_iterations次
        for iteration in range(1, self.max_iterations + 1):
            iterations = iteration
            self._timing("before_iteration", started)
            controller_stop = self._controller_fault("before_iteration")
            if controller_stop is not None:
                stop_reason = controller_stop
                break
            if time.perf_counter() - started >= self.total_timeout_seconds:
                stop_reason = "total_timeout"
                self._timing("deadline_detected", started)
                break
            pending_model_decision: dict[str, Any] | None = None
            skip_iteration = False
            while True:
                model_started = time.perf_counter()
                try:
                    #controller把当前issue+history+tools告诉llm,然后问下一步干什么
                    turn = self.model.next_action(
                        issue=self.issue,
                        history=history,
                        tool_schemas=self.tools.schemas,
                    )
                except Exception as exc:
                    if pending_model_decision is not None:
                        self._record_outcome(
                            pending_model_decision,
                            outcome="retry_failed",
                            revision=self.tools.revision,
                            success=False,
                            iteration=iteration,
                        )
                        pending_model_decision = None
                    failure = failure_from_exception(exc)
                    final_message = redact_text(f"Model error: {type(exc).__name__}: {exc}")
                    error_event = self.recorder.record(
                        "model_error",
                        iteration=iteration,
                        injected=failure.injected,
                        fault_type=failure.fault_type,
                        error=asdict(failure),
                        latency_ms=(time.perf_counter() - model_started) * 1000,
                    )
                    controller_stop = self._controller_fault("before_recovery")
                    remaining = 0.0 if controller_stop == "total_timeout" else self._remaining_deadline_ms(started)
                    budget = self.recovery_policy.max_model_retries - model_retries
                    if controller_stop == "retry_budget_exhausted":
                        budget = 0
                    decision = self.recovery_policy.decide(
                        failure,
                        attempt=model_retries + 1,
                        remaining_deadline_ms=remaining,
                        remaining_budget=budget,
                        revision_before=self.tools.revision,
                        revision_after=self.tools.revision,
                    )
                    decision_event = self._record_decision(decision, evidence_event_id=error_event["event_id"], iteration=iteration)
                    if decision.action == "retry_model":
                        model_retries += 1
                        pending_model_decision = decision_event
                        continue
                    if decision.action == "return_to_model":
                        self._record_outcome(
                            decision_event,
                            outcome="returned_to_model",
                            revision=self.tools.revision,
                            success=False,
                            iteration=iteration,
                        )
                        history.append({"type": "model_error", "error": failure.message})
                        skip_iteration = True
                        break
                    stop_reason = "total_timeout" if decision.action == "stop_total_timeout" else "model_error"
                    self._record_outcome(
                        decision_event,
                        outcome="bounded_stop",
                        revision=self.tools.revision,
                        success=False,
                        iteration=iteration,
                    )
                    forced_stop = stop_reason
                    break
                if pending_model_decision is not None:
                    self._record_outcome(
                        pending_model_decision,
                        outcome="recovered",
                        revision=self.tools.revision,
                        success=True,
                        iteration=iteration,
                    )
                break
            if forced_stop is not None:
                break
            if skip_iteration:
                continue
            usage = usage.add(turn.usage)
            action = turn.action
            model_error = asdict(turn.failure) if turn.failure else None
            model_event = self.recorder.record(
                "model_turn",
                iteration=iteration,
                action=asdict(action),
                injected=bool(turn.failure and turn.failure.injected),
                fault_type=turn.failure.fault_type if turn.failure else None,
                error=model_error,
                latency_ms=turn.latency_ms,
                token_usage=asdict(turn.usage),
            )

            if turn.failure is not None:
                decision = self.recovery_policy.decide(
                    turn.failure,
                    attempt=1,
                    remaining_deadline_ms=self._remaining_deadline_ms(started),
                    remaining_budget=0,
                    revision_before=self.tools.revision,
                    revision_after=self.tools.revision,
                )
                decision_event = self._record_decision(decision, evidence_event_id=model_event["event_id"], iteration=iteration)
                self._record_outcome(
                    decision_event,
                    outcome="returned_to_model" if decision.action == "return_to_model" else "bounded_stop",
                    revision=self.tools.revision,
                    success=False,
                    iteration=iteration,
                )
                if decision.action in {"stop", "stop_total_timeout"}:
                    stop_reason = "total_timeout" if decision.action == "stop_total_timeout" else "model_error"
                    break

            if action.kind == "plan":
                history.append({"type": "plan", "content": action.content})
                continue
            if action.kind == "final":
                final_message = action.content
                stop_reason = "model_final"
                history.append({"type": "final", "content": action.content})
                break
            if action.kind != "tool" or not action.tool_name:
                history.append({"type": "tool_error", "error": "invalid model action"})
                self.tools.unnecessary_calls += 1
                continue

            if first_passing_revision is not None and action.tool_name != "git_diff":
                self.tools.unnecessary_calls += 1
            arguments = action.arguments or {}
            #执行llm选中的工具
            call_revision_before = self.tools.revision
            result = self.tools.call(action.tool_name, arguments)
            tool_calls += 1
            tool_event = self._record_tool(
                iteration=iteration,
                name=action.tool_name,
                arguments=arguments,
                result=result,
                origin="model",
                revision_before_override=call_revision_before,
            )
            revision_before = tool_event["workspace_revision_before"]
            if result.failure is not None:
                controller_stop = self._controller_fault("before_recovery")
                remaining = 0.0 if controller_stop == "total_timeout" else self._remaining_deadline_ms(started)
                retry_key = (action.tool_name, result.failure.fault_type or result.failure.code)
                used = read_retries.get(retry_key, 0)
                if action.tool_name == "run_tests" and result.failure.code == "timeout":
                    budget = self.recovery_policy.max_test_timeout_retries - test_timeout_retries
                else:
                    budget = self.recovery_policy.max_read_only_retries - used
                if controller_stop == "retry_budget_exhausted":
                    budget = 0
                decision = self.recovery_policy.decide(
                    result.failure,
                    attempt=used + 1,
                    remaining_deadline_ms=remaining,
                    remaining_budget=budget,
                    revision_before=int(revision_before or 0),
                    revision_after=result.revision,
                    tool_name=action.tool_name,
                )
                decision_event = self._record_decision(decision, evidence_event_id=tool_event["event_id"], iteration=iteration)
                if decision.action in {"retry_read_only", "rerun_tests"}:
                    if decision.action == "rerun_tests":
                        test_timeout_retries += 1
                    else:
                        read_retries[retry_key] = used + 1
                    result = self.tools.call(action.tool_name, arguments)
                    tool_calls += 1
                    self._record_tool(
                        iteration=iteration,
                        name=action.tool_name,
                        arguments=arguments,
                        result=result,
                        origin="recovery",
                    )
                    self._record_outcome(
                        decision_event,
                        outcome="recovered" if result.failure is None else "retry_failed",
                        revision=result.revision,
                        success=result.failure is None,
                        iteration=iteration,
                    )
                elif decision.action == "reconcile_mutation":
                    diff_result = self.tools.call("git_diff", {})
                    tool_calls += 1
                    self._record_tool(
                        iteration=iteration,
                        name="git_diff",
                        arguments={},
                        result=diff_result,
                        origin="recovery_reconciliation",
                    )
                    changed = bool(
                        diff_result.ok
                        and result.revision > int(revision_before or 0)
                        and diff_result.observation.get("changed_files")
                    )
                    last_diff = diff_result.observation if diff_result.ok else {}
                    last_diff_revision = diff_result.revision if diff_result.ok else None
                    if changed:
                        result = ToolResult(
                            True,
                            {"changed": True, "reconciled": True, "changed_files": diff_result.observation.get("changed_files", [])},
                            result.latency_ms + diff_result.latency_ms,
                            diff_result.revision,
                        )
                    self._record_outcome(
                        decision_event,
                        outcome="mutation_confirmed" if changed else "mutation_not_confirmed",
                        revision=self.tools.revision,
                        success=changed,
                        iteration=iteration,
                    )
                else:
                    self._record_outcome(
                        decision_event,
                        outcome="returned_to_model" if decision.action == "return_to_model" else "bounded_stop",
                        revision=self.tools.revision,
                        success=False,
                        iteration=iteration,
                    )
                    if decision.action in {"stop", "stop_total_timeout"}:
                        stop_reason = "total_timeout" if decision.action == "stop_total_timeout" else "tool_error"
                        forced_stop = stop_reason
            history.append(
                {
                    "type": "tool_result",
                    "tool": action.tool_name,
                    "arguments": arguments,
                    "ok": result.ok,
                    "observation": result.observation,
                    "error": result.error,
                    "workspace_revision": result.revision,
                }
            )
            if forced_stop is not None:
                break
            if action.tool_name == "run_tests" and result.ok:
                last_test = result.observation
                last_test_revision = result.revision
                if result.observation.get("passed"):
                    first_passing_revision = result.revision
                    if result.revision > 0:
                        stop_reason = "tests_passed"
                        final_message = "Configured tests passed on the modified revision."
                        break
                elif result.revision > 0:
                    repair_cycles += 1
                    if repair_cycles >= self.max_repair_cycles:
                        stop_reason = "repair_limit"
                        break
            elif action.tool_name == "git_diff" and result.ok:
                last_diff = result.observation
                last_diff_revision = result.revision

        self._timing("finalization_started", started)
        if last_test_revision != self.tools.revision:
            last_test_result = self.tools.call("run_tests", {})
            tool_calls += 1
            self._record_tool(
                iteration=iterations,
                name="run_tests",
                arguments={},
                result=last_test_result,
                origin="controller",
            )
            if last_test_result.ok:
                last_test = last_test_result.observation

        if last_diff_revision != self.tools.revision:
            diff_result = self.tools.call("git_diff", {})
            tool_calls += 1
            self._record_tool(
                iteration=iterations,
                name="git_diff",
                arguments={},
                result=diff_result,
                origin="controller",
            )
            last_diff = diff_result.observation if diff_result.ok else {}
        self._timing("finalization_completed", started)
        final_diff = (last_diff or {}).get("diff", "")
        changed_files = (last_diff or {}).get("changed_files", [])
        success = bool(last_test and last_test.get("passed"))
        latency_ms = (time.perf_counter() - started) * 1000
        self.recorder.record(
            "run_finished",
            injected=self.fault_runtime is not None,
            fault_injection=self.fault_runtime is not None,
            success=success,
            stop_reason=stop_reason,
            iterations=iterations,
            repair_cycles=repair_cycles,
            tool_calls=tool_calls,
            unnecessary_tool_calls=self.tools.unnecessary_calls,
            latency_ms=latency_ms,
            token_usage=asdict(usage),
            final_test=last_test,
            changed_files=changed_files,
        )
        return RunResult(
            run_id=run_id,
            success=success,
            stop_reason=stop_reason,
            iterations=iterations,
            repair_cycles=repair_cycles,
            tool_calls=tool_calls,
            unnecessary_tool_calls=self.tools.unnecessary_calls,
            latency_ms=latency_ms,
            usage=usage,
            final_message=final_message,
            final_diff=final_diff,
            changed_files=changed_files,
            final_test=last_test,
            trajectory_path=str(self.recorder.path),
        )

#负责把“整个运行环境搭起来,然后启动agent loop”
def run_agent(config: RunConfig, model: ModelClient, *, run_id: str | None = None) -> tuple[RunResult, Path]:
    overall_started = time.perf_counter()
    run_id = run_id or uuid.uuid4().hex
    session = RunSession.start(config, run_id=run_id, model_metadata=model.metadata)
    try:
        loop = AgentLoop(
            issue=config.issue,
            model=model,
            tools=session.tools,
            recorder=session.recorder,
            max_iterations=config.limits.max_iterations,
            max_repair_cycles=config.limits.max_repair_cycles,
            total_timeout_seconds=config.limits.total_timeout_seconds,
        )
        result = loop.run(run_id, started_at=overall_started)
    finally:
        session.close()
    result_path = session.run_directory / "run.json"
    trace_summary = summarize_trace(session.trajectory_path)
    result_payload = {
        **asdict(result),
        "evaluation_profile": config.evaluation_profile,
        "trace_summary": trace_summary,
    }
    result_payload["trace_reconciliation"] = reconcile_summary(trace_summary, result_payload)
    result_path.write_text(
        json.dumps(redact_value(result_payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result, session.workspace
