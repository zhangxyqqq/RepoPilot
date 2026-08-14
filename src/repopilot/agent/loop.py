from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from repopilot.config import RunConfig
from repopilot.llm.base import ModelClient
from repopilot.models import RunResult, TokenUsage, ToolResult
from repopilot.sandbox import DockerSandbox, stage_repository
from repopilot.tools import ToolRegistry
from repopilot.trajectory import TrajectoryRecorder


class AgentLoop:
    def __init__(
        self,
        *,
        issue: str,
        model: ModelClient,
        tools: ToolRegistry,
        recorder: TrajectoryRecorder,
        max_iterations: int,
        max_repair_cycles: int,
        total_timeout_seconds: int,
    ):
        self.issue = issue
        self.model = model
        self.tools = tools
        self.recorder = recorder
        self.max_iterations = max_iterations
        self.max_repair_cycles = max_repair_cycles
        self.total_timeout_seconds = total_timeout_seconds

    def _record_tool(
        self,
        *,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        origin: str,
    ) -> None:
        self.recorder.record(
            "tool_call",
            iteration=iteration,
            origin=origin,
            tool=name,
            arguments=arguments,
            ok=result.ok,
            observation=result.observation,
            error=result.error,
            latency_ms=result.latency_ms,
            workspace_revision=result.revision,
        )

    def run(self, run_id: str, *, started_at: float | None = None) -> RunResult:
        started = started_at if started_at is not None else time.perf_counter()
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

        for iteration in range(1, self.max_iterations + 1):
            iterations = iteration
            if time.perf_counter() - started >= self.total_timeout_seconds:
                stop_reason = "total_timeout"
                break
            try:
                turn = self.model.next_action(
                    issue=self.issue,
                    history=history,
                    tool_schemas=self.tools.schemas,
                )
            except Exception as exc:
                stop_reason = "model_error"
                final_message = f"Model error: {type(exc).__name__}: {exc}"
                self.recorder.record("model_error", iteration=iteration, error=final_message)
                break
            usage = usage.add(turn.usage)
            action = turn.action
            self.recorder.record(
                "model_turn",
                iteration=iteration,
                action=asdict(action),
                latency_ms=turn.latency_ms,
                token_usage=asdict(turn.usage),
            )

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
            result = self.tools.call(action.tool_name, arguments)
            tool_calls += 1
            self._record_tool(
                iteration=iteration,
                name=action.tool_name,
                arguments=arguments,
                result=result,
                origin="model",
            )
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
        final_diff = (last_diff or {}).get("diff", "")
        changed_files = (last_diff or {}).get("changed_files", [])
        success = bool(last_test and last_test.get("passed"))
        latency_ms = (time.perf_counter() - started) * 1000
        self.recorder.record(
            "run_finished",
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


def run_agent(config: RunConfig, model: ModelClient, *, run_id: str | None = None) -> tuple[RunResult, Path]:
    overall_started = time.perf_counter()
    run_id = run_id or uuid.uuid4().hex
    run_directory = config.output_dir.resolve() / run_id
    if run_directory.exists():
        raise FileExistsError(f"run output already exists: {run_directory}")
    workspace = stage_repository(config.repository, run_directory / "workspace")
    trajectory_path = run_directory / "trajectory.jsonl"
    recorder = TrajectoryRecorder(
        trajectory_path,
        run_id=run_id,
        metadata={
            "issue": config.issue,
            "repository": str(config.repository.resolve()),
            "test_command": list(config.test_command),
            "model": model.metadata,
            "limits": asdict(config.limits),
            "sandbox": asdict(config.sandbox),
        },
    )
    sandbox = DockerSandbox(
        workspace,
        test_command=config.test_command,
        command_timeout_seconds=config.limits.command_timeout_seconds,
        config=config.sandbox,
    )
    try:
        sandbox.start()
        tools = ToolRegistry(sandbox)
        loop = AgentLoop(
            issue=config.issue,
            model=model,
            tools=tools,
            recorder=recorder,
            max_iterations=config.limits.max_iterations,
            max_repair_cycles=config.limits.max_repair_cycles,
            total_timeout_seconds=config.limits.total_timeout_seconds,
        )
        result = loop.run(run_id, started_at=overall_started)
    finally:
        sandbox.close()
    result_path = run_directory / "run.json"
    result_path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result, workspace
