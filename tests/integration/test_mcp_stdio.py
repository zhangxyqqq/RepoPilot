from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from repopilot.config import RunConfig
from repopilot.evaluation.cases import load_cases
from repopilot.evaluation.metrics import parse_pytest_counts
from repopilot.mcp import normalize_tool_result
from repopilot.sandbox import DockerSandbox, stage_repository
from repopilot.session import RunSession
from repopilot.trajectory import TraceReader


ROOT = Path.cwd().resolve()
REPOSITORY = (ROOT / "benchmarks/cases/arithmetic_edge_case/repo").resolve()


def _parameters(output: Path, *, issue: str = "Inspect safely") -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "repopilot.cli", "mcp-serve", str(REPOSITORY),
            "--issue", issue, "--output", str(output),
        ],
        cwd=str(ROOT),
    )


@pytest.mark.docker
def test_stdio_initialize_list_call_validation_security_and_clean_eof(tmp_path: Path) -> None:
    secret = "phase-three-secret-12345"
    responses: list[dict[str, object]] = []

    async def exercise() -> None:
        async with stdio_client(_parameters(tmp_path / "runs")) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "repopilot"
                listed = await client.list_tools()
                assert [tool.name for tool in listed.tools] == [
                    "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff"
                ]
                calls = [
                    ("unknown", {}),
                    ("search_code", {}),
                    ("read_file", {"path": "calculator.py", "extra": True}),
                    ("read_file", {"path": 42}),
                    ("read_file", {"path": "../../etc/passwd"}),
                    ("read_file", {"path": "/etc/passwd"}),
                    ("run_tests", {"command": ["sh", "-c", "id"]}),
                    ("apply_patch", {"patch": "not a patch"}),
                    ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: tests/test_calculator.py\n@@\n-x\n+y\n*** End Patch"}),
                    ("read_file", {"path": "calculator.py", "api_key": secret}),
                ]
                for name, arguments in calls:
                    result = await client.call_tool(name, arguments)
                    assert result.isError is True
                    responses.append(result.structuredContent or {})
                healthy = await client.call_tool("git_diff", {})
                assert healthy.isError is False
                responses.append(healthy.structuredContent or {})

    anyio.run(exercise)

    artifacts = list((tmp_path / "runs").glob("mcp-*/trajectory.jsonl"))
    assert len(artifacts) == 1
    reader = TraceReader(artifacts[0])
    assert list(reader)
    assert reader.complete is True
    combined = json.dumps(responses, sort_keys=True) + artifacts[0].read_text(encoding="utf-8")
    assert secret not in combined
    assert str(REPOSITORY) not in combined
    assert "/etc/passwd" not in combined
    assert "_init_repo" not in combined


@pytest.mark.docker
def test_stdio_malformed_message_exits_cleanly_on_eof(tmp_path: Path) -> None:
    command = [sys.executable, "-m", "repopilot.cli", "mcp-serve", str(REPOSITORY), "--issue", "Inspect", "--output", str(tmp_path / "runs")]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input="this is not json-rpc\n",
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0
    messages = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert messages
    assert all(message.get("method") == "notifications/message" for message in messages)
    assert "Traceback" not in completed.stdout
    trajectories = list((tmp_path / "runs").glob("mcp-*/trajectory.jsonl"))
    assert len(trajectories) == 1
    reader = TraceReader(trajectories[0])
    assert list(reader)
    assert reader.complete is True


@pytest.mark.docker
def test_complete_controlled_tool_sequence_matches_direct_over_real_stdio_mcp(tmp_path: Path) -> None:
    case = next(case for case in load_cases(Path("benchmarks/cases")) if case.case_id == "arithmetic_edge_case")
    direct_session = RunSession.start(
        RunConfig(case.repository, case.issue, tmp_path / "direct", case.test_command),
        run_id="direct-tools",
        model_metadata={"provider": "test", "model": "direct", "deterministic": True},
    )
    calls = [
        ("list_files", {}),
        ("search_code", {"query": case.search_query}),
        ("read_file", {"path": case.expected_fix_files[0]}),
        ("apply_patch", {"patch": case.solution_patch}),
        ("run_tests", {}),
        ("git_diff", {}),
    ]

    def comparable(value: dict[str, object]) -> dict[str, object]:
        normalized = json.loads(json.dumps(value))
        normalized.pop("latency_ms", None)
        observation = normalized.get("observation")
        if isinstance(observation, dict) and "output" in observation:
            observation["output"] = parse_pytest_counts(str(observation["output"]))
        if isinstance(observation, dict):
            repository_context = observation.get("repository_context")
            if isinstance(repository_context, dict):
                retrieval = repository_context.get("retrieval")
                if isinstance(retrieval, dict):
                    retrieval.pop("latency_ms", None)
        return normalized

    mcp_results: dict[str, dict[str, object]] = {}

    async def exercise() -> None:
        async with stdio_client(_parameters(tmp_path / "mcp", issue=case.issue)) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                for name, arguments in calls:
                    direct = normalize_tool_result(direct_session.tools.call(name, arguments))
                    transported = await client.call_tool(name, arguments)
                    assert comparable(transported.structuredContent or {}) == comparable(direct)
                    mcp_results[name] = dict(transported.structuredContent or {})

    try:
        anyio.run(exercise)
    finally:
        direct_session.close()

    mcp_workspaces = list((tmp_path / "mcp").glob("mcp-*/workspace"))
    assert len(mcp_workspaces) == 1

    def hidden(workspace: Path, destination: Path) -> dict[str, object]:
        staged = stage_repository(workspace, destination)
        shutil.rmtree(staged / "tests")
        stage_repository(case.hidden_tests, staged / "tests")
        with DockerSandbox(staged, test_command=case.test_command, command_timeout_seconds=30) as sandbox:
            response = sandbox.invoke("run_tests", {})
        assert response["ok"], response.get("error")
        return response["result"]

    direct_hidden = hidden(direct_session.workspace, tmp_path / "direct-hidden")
    mcp_hidden = hidden(mcp_workspaces[0], tmp_path / "mcp-hidden")
    assert mcp_results["run_tests"]["observation"]["passed"] is True  # type: ignore[index]
    assert direct_hidden["passed"] is mcp_hidden["passed"] is True
    assert mcp_results["apply_patch"]["revision"] == mcp_results["git_diff"]["revision"] == 1
