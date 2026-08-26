import json
from dataclasses import asdict
from pathlib import Path

from repopilot.models import RunResult, TokenUsage
from repopilot.tools import PUBLIC_TOOL_DEFINITIONS, ToolRegistry
from repopilot.tools.contracts import TOOL_SCHEMAS


FIXTURES = Path("tests/fixtures/v1")


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_six_public_tool_schemas_match_v1_golden_fixture():
    expected = _fixture("tool_schemas.json")

    assert TOOL_SCHEMAS == expected
    assert [definition.provider_schema() for definition in PUBLIC_TOOL_DEFINITIONS] == expected
    assert [tool["name"] for tool in expected] == [
        "list_files",
        "search_code",
        "read_file",
        "apply_patch",
        "run_tests",
        "git_diff",
    ]


def test_v1_run_fields_remain_present():
    expected = set(_fixture("report_fields.json")["run_required_fields"])
    result = RunResult(
        run_id="fixture",
        success=True,
        stop_reason="tests_passed",
        iterations=1,
        repair_cycles=0,
        tool_calls=1,
        unnecessary_tool_calls=0,
        latency_ms=1.0,
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        final_message="done",
        final_diff="diff",
        changed_files=["module.py"],
        final_test={"passed": True},
        trajectory_path="trajectory.jsonl",
    )

    assert expected <= set(asdict(result))


def test_phase_zero_manifest_records_a_passing_restored_baseline():
    baseline = _fixture("baseline_manifest.json")["restored_v1_compatibility"]

    assert baseline["full_test_suite"] == {"failed": 0, "passed": 67}
    assert baseline["docker_patch_and_security_test"]["passed"] == 1
    assert baseline["deterministic_benchmark"]["tasks_succeeded"] == 12
    assert baseline["deterministic_benchmark"]["public_test_cases_passed"] == 12
    assert baseline["deterministic_benchmark"]["hidden_test_cases_passed"] == 12
    assert baseline["deterministic_benchmark"]["localization_f1_mean"] == 1.0


class _GoldenSandbox:
    def __init__(self, observations):
        self.observations = observations

    def invoke(self, name, arguments):
        if name == "read_file" and arguments.get("path") == "../../etc/passwd":
            return {
                "ok": False,
                "error": "ValueError: path must remain inside the repository workspace",
                "latency_ms": 1.25,
            }
        return {"ok": True, "result": self.observations[name], "latency_ms": 1.25}


def test_representative_direct_tool_results_and_errors_match_v1_golden_fixture():
    golden = _fixture("direct_tool_results.json")
    observations = {item["name"]: item["observation"] for item in golden["successes"]}
    registry = ToolRegistry(_GoldenSandbox(observations))  # type: ignore[arg-type]

    for expected in golden["successes"]:
        result = registry.call(expected["name"], expected["arguments"])
        assert result.ok is True
        assert result.observation == expected["observation"]
        assert result.revision == expected["revision"]

    for expected in golden["errors"]:
        isolated = ToolRegistry(_GoldenSandbox(observations))  # type: ignore[arg-type]
        result = isolated.call(expected["name"], expected["arguments"])
        assert result.ok is expected["ok"]
        assert result.error == expected["error"]
        assert result.revision == expected["revision"]
