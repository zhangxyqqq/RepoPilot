from pathlib import Path

from repopilot.agent.loop import AgentLoop
from repopilot.models import AgentAction, ModelTurn, TokenUsage, ToolResult
from repopilot.trajectory import TrajectoryRecorder


class FakeModel:
    def __init__(self):
        self.actions = iter(
            [
                AgentAction("tool", "apply_patch", {"patch": "change"}),
                AgentAction("tool", "run_tests", {}),
                AgentAction("tool", "list_files", {}),
            ]
        )
        self.calls = 0

    @property
    def metadata(self):
        return {"provider": "fake", "model": "fake", "deterministic": True}

    def next_action(self, **kwargs):
        self.calls += 1
        return ModelTurn(next(self.actions), latency_ms=0, usage=TokenUsage())


class FakeTools:
    schemas = []

    def __init__(self):
        self.revision = 0
        self.unnecessary_calls = 0
        self.calls: list[str] = []

    def call(self, name, arguments):
        self.calls.append(name)
        if name == "apply_patch":
            self.revision = 1
            observation = {"changed": True, "paths": ["module.py"]}
        elif name == "run_tests":
            observation = {"passed": True, "exit_code": 0, "output": "1 passed"}
        elif name == "git_diff":
            observation = {"diff": "diff", "changed_files": ["module.py"]}
        else:
            raise AssertionError(f"unexpected post-success tool call: {name}")
        return ToolResult(True, observation, latency_ms=0, revision=self.revision)


def test_controller_stops_after_modified_revision_passes_tests(tmp_path: Path):
    model = FakeModel()
    tools = FakeTools()
    recorder = TrajectoryRecorder(tmp_path / "trajectory.jsonl", run_id="run", metadata={})
    loop = AgentLoop(
        issue="fix the bug",
        model=model,
        tools=tools,
        recorder=recorder,
        max_iterations=30,
        max_repair_cycles=3,
        total_timeout_seconds=300,
    )

    result = loop.run("run")

    assert result.success is True
    assert result.stop_reason == "tests_passed"
    assert result.iterations == 2
    assert model.calls == 2
    assert tools.calls == ["apply_patch", "run_tests", "git_diff"]
