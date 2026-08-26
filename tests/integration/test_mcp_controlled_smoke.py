from __future__ import annotations

import shutil
import time
from pathlib import Path

import anyio
import pytest

from repopilot.agent.loop import AgentLoop, run_agent
from repopilot.config import RunConfig
from repopilot.evaluation.cases import load_cases
from repopilot.mcp import McpToolAdapter
from repopilot.models import ToolResult
from repopilot.sandbox import DockerSandbox, stage_repository
from repopilot.session import RunSession
from repopilot.trajectory import summarize_trace


class _McpAdapterBackend:
    """Synchronous controller view over the MCP result adapter for smoke testing."""

    def __init__(self, adapter: McpToolAdapter):
        self.adapter = adapter

    @property
    def definitions(self):
        return self.adapter.backend.definitions

    @property
    def schemas(self):
        return self.adapter.backend.schemas

    @property
    def revision(self):
        return self.adapter.backend.revision

    @property
    def unnecessary_calls(self):
        return self.adapter.backend.unnecessary_calls

    @unnecessary_calls.setter
    def unnecessary_calls(self, value):
        self.adapter.backend.unnecessary_calls = value

    def call(self, name, arguments):
        transported = anyio.run(self.adapter.call_tool, name, arguments)
        value = transported.structuredContent or {}
        error = value.get("error")
        return ToolResult(
            ok=bool(value.get("ok")),
            observation=dict(value.get("observation") or {}),
            latency_ms=float(value.get("latency_ms", 0)),
            revision=int(value.get("revision", self.revision)),
            error=str(error.get("message")) if isinstance(error, dict) else None,
        )


def _hidden_result(case, workspace: Path, destination: Path) -> dict[str, object]:
    staged = stage_repository(workspace, destination)
    shutil.rmtree(staged / "tests")
    stage_repository(case.hidden_tests, staged / "tests")
    with DockerSandbox(staged, test_command=case.test_command, command_timeout_seconds=30) as sandbox:
        result = sandbox.invoke("run_tests", {})
    assert result["ok"], result.get("error")
    return result["result"]


@pytest.mark.docker
def test_one_controlled_task_matches_direct_via_mcp_adapter(tmp_path: Path) -> None:
    case = next(case for case in load_cases(Path("benchmarks/cases")) if case.case_id == "arithmetic_edge_case")
    direct_config = RunConfig(case.repository, case.issue, tmp_path / "direct", case.test_command)
    direct_result, direct_workspace = run_agent(direct_config, case.scripted_model(), run_id="direct")

    mcp_config = RunConfig(case.repository, case.issue, tmp_path / "mcp", case.test_command)
    session = RunSession.start(
        mcp_config,
        run_id="mcp-adapter",
        model_metadata=case.scripted_model().metadata,
        transport="mcp_adapter_smoke",
    )
    backend = _McpAdapterBackend(McpToolAdapter(session.tools))
    try:
        mcp_result = AgentLoop(
            issue=case.issue,
            model=case.scripted_model(),
            tools=backend,
            recorder=session.recorder,
            max_iterations=mcp_config.limits.max_iterations,
            max_repair_cycles=mcp_config.limits.max_repair_cycles,
            total_timeout_seconds=mcp_config.limits.total_timeout_seconds,
        ).run(session.run_id, started_at=time.perf_counter())
    finally:
        session.close()

    direct_hidden = _hidden_result(case, direct_workspace, tmp_path / "direct-hidden")
    mcp_hidden = _hidden_result(case, session.workspace, tmp_path / "mcp-hidden")
    direct_revision = summarize_trace(Path(direct_result.trajectory_path))["workspace_revisions"]["final"]
    mcp_revision = summarize_trace(Path(mcp_result.trajectory_path))["workspace_revisions"]["final"]

    assert direct_result.final_diff == mcp_result.final_diff
    assert direct_result.final_test and mcp_result.final_test
    assert direct_result.final_test["passed"] is mcp_result.final_test["passed"] is True
    assert direct_result.final_test["exit_code"] == mcp_result.final_test["exit_code"] == 0
    assert direct_hidden["passed"] is mcp_hidden["passed"] is True
    assert direct_revision == mcp_revision == session.tools.revision == 1
    assert direct_result.tool_calls == mcp_result.tool_calls == 6
    assert direct_result.unnecessary_tool_calls == mcp_result.unnecessary_tool_calls == 0
