from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from repopilot.agent import run_agent
from repopilot.config import RunConfig
from repopilot.evaluation.cases import BenchmarkCase, load_cases
from repopilot.evaluation.metrics import aggregate, localization_metrics, parse_pytest_counts
from repopilot.evaluation.reports import write_reports
from repopilot.llm.base import ModelClient
from repopilot.sandbox import DockerSandbox, stage_repository


def _run_hidden_evaluation(case: BenchmarkCase, agent_workspace: Path, destination: Path) -> dict[str, object]:
    evaluation_workspace = stage_repository(agent_workspace, destination)
    public_tests = evaluation_workspace / "tests"
    if public_tests.exists():
        shutil.rmtree(public_tests)
    stage_repository(case.hidden_tests, public_tests)
    with DockerSandbox(
        evaluation_workspace,
        test_command=case.test_command,
        command_timeout_seconds=30,
    ) as sandbox:
        result = sandbox.invoke("run_tests", {})
    if not result.get("ok"):
        return {"passed": False, "exit_code": None, "output": result.get("error", "sandbox error"), "timed_out": False}
    return result["result"]


def _test_result(raw: dict[str, object] | None) -> dict[str, object]:
    raw = raw or {}
    return {
        "passed": bool(raw.get("passed")),
        "counts": parse_pytest_counts(str(raw.get("output", ""))),
        "exit_code": raw.get("exit_code"),
        "timed_out": bool(raw.get("timed_out", False)),
    }


def evaluate_benchmarks(
    benchmark_root: Path,
    output_directory: Path,
    *,
    model_factory: Callable[[BenchmarkCase], ModelClient] | None = None,
) -> dict[str, object]:
    cases = load_cases(benchmark_root)
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    model_factory = model_factory or (lambda case: case.scripted_model())
    case_results: list[dict[str, object]] = []

    for case in cases:
        model = model_factory(case)
        config = RunConfig(
            repository=case.repository,
            issue=case.issue,
            output_dir=output_directory / "runs",
            test_command=case.test_command,
        )
        agent_result, workspace = run_agent(config, model, run_id=case.case_id)
        hidden_result = _run_hidden_evaluation(
            case,
            workspace,
            output_directory / "evaluation-workspaces" / case.case_id,
        )
        localization = localization_metrics(agent_result.changed_files, case.allowed_fix_sets)
        public_tests = _test_result(agent_result.final_test)
        hidden_tests = _test_result(hidden_result)
        case_result: dict[str, object] = {
            "id": case.case_id,
            "issue": case.issue,
            "task_success": bool(public_tests["passed"] and hidden_tests["passed"]),
            "public_tests": public_tests,
            "hidden_tests": hidden_tests,
            "expected_fix_files": list(case.expected_fix_files),
            "changed_files": agent_result.changed_files,
            "localization_precision": localization["precision"],
            "localization_recall": localization["recall"],
            "localization_f1": localization["f1"],
            "tool_calls": agent_result.tool_calls,
            "unnecessary_tool_calls": agent_result.unnecessary_tool_calls,
            "iterations": agent_result.iterations,
            "repair_cycles": agent_result.repair_cycles,
            "stop_reason": agent_result.stop_reason,
            "latency_ms": agent_result.latency_ms,
            "token_usage": asdict(agent_result.usage),
            "trajectory_path": agent_result.trajectory_path,
            "final_diff": agent_result.final_diff,
            "model": model.metadata,
        }
        case_results.append(case_result)
        case_path = output_directory / "runs" / case.case_id / "evaluation.json"
        case_path.write_text(json.dumps(case_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report: dict[str, object] = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_root": str(benchmark_root.resolve()),
        "evaluation_mode": "deterministic" if all(case["model"]["deterministic"] for case in case_results) else "live",
        "cases": case_results,
        "aggregate": aggregate(case_results),
    }
    json_path, markdown_path = write_reports(report, output_directory)
    report["report_paths"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report
