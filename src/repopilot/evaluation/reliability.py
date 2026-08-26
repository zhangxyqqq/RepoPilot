from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repopilot.agent.loop import AgentLoop, run_agent
from repopilot.agent.recovery import RecoveryPolicy
from repopilot.config import RunConfig
from repopilot.evaluation.cases import BenchmarkCase, load_cases
from repopilot.evaluation.faults import (
    FaultInjectingBackend,
    FaultInjectingModel,
    FaultRuntime,
    FaultSchedule,
    ReliabilityScenario,
    load_fault_schedule,
)
from repopilot.evaluation.metrics import parse_pytest_counts
from repopilot.evaluation.profiles import EvaluationProfile, evaluate_profile_acceptance, load_evaluation_profile
from repopilot.evaluation.taxonomy import classify_failures, load_failure_taxonomy
from repopilot.models import AgentAction, RunResult
from repopilot.sandbox import DockerSandbox, stage_repository
from repopilot.session import RunSession
from repopilot.trajectory import TraceReader, reconcile_summary, redact_value, summarize_trace


_SOURCE_PROFILE = Path(__file__).resolve().parents[3] / "configs" / "evaluation" / "reliability.json"
_PACKAGED_PROFILE = Path(__file__).resolve().parents[1] / "configs" / "evaluation" / "reliability.json"
DEFAULT_RELIABILITY_PROFILE = _SOURCE_PROFILE if _SOURCE_PROFILE.exists() else _PACKAGED_PROFILE
_SOURCE_SCHEDULE = Path(__file__).resolve().parents[3] / "configs" / "faults" / "reliability_matrix.v1.json"
_PACKAGED_SCHEDULE = Path(__file__).resolve().parents[1] / "configs" / "faults" / "reliability_matrix.v1.json"
DEFAULT_FAULT_SCHEDULE = _SOURCE_SCHEDULE if _SOURCE_SCHEDULE.exists() else _PACKAGED_SCHEDULE


def _actions(case: BenchmarkCase, scenario: ReliabilityScenario | None = None) -> list[AgentAction]:
    target = case.expected_fix_files[0]
    actions = [
        AgentAction("tool", "list_files", {}),
        AgentAction("tool", "search_code", {"query": case.search_query}),
        AgentAction("tool", "read_file", {"path": target, "start_line": 1, "end_line": 200}),
        AgentAction("plan", content=f"PLAN: update {target} with the minimal behavior fix, then run tests."),
        AgentAction("tool", "apply_patch", {"patch": case.solution_patch}),
        AgentAction("tool", "run_tests", {}),
        AgentAction("tool", "git_diff", {}),
        AgentAction("final", content="FINAL: implemented the focused fix and verified the public tests."),
    ]
    if scenario and scenario.correction == "repeat_faulted_action":
        tool = next(fault.tool for fault in scenario.faults if fault.boundary == "tool")
        index = next(index for index, action in enumerate(actions) if action.tool_name == tool)
        actions.insert(index + 1, actions[index])
    return actions


def _hidden(case: BenchmarkCase, workspace: Path, destination: Path) -> dict[str, Any]:
    staged = stage_repository(workspace, destination)
    tests = staged / "tests"
    if tests.exists():
        shutil.rmtree(tests)
    stage_repository(case.hidden_tests, tests)
    with DockerSandbox(staged, test_command=case.test_command, command_timeout_seconds=30) as sandbox:
        response = sandbox.invoke("run_tests", {})
    if not response.get("ok"):
        return {"passed": False, "exit_code": None, "output": "hidden harness failure", "timed_out": False, "harness_failed": True}
    return dict(response["result"])


def _test_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    return {
        "passed": bool(raw.get("passed")),
        "counts": parse_pytest_counts(str(raw.get("output", ""))),
        "exit_code": raw.get("exit_code"),
        "timed_out": bool(raw.get("timed_out")),
        "harness_failed": bool(raw.get("harness_failed")),
    }


def _write_run_artifact(session: RunSession, result: RunResult, profile: EvaluationProfile) -> dict[str, Any]:
    summary = summarize_trace(session.trajectory_path)
    payload = {**asdict(result), "evaluation_profile": profile.artifact(), "trace_summary": summary}
    payload["trace_reconciliation"] = reconcile_summary(summary, payload)
    (session.run_directory / "run.json").write_text(
        json.dumps(redact_value(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _token_delta(value: dict[str, Any], baseline: dict[str, Any]) -> dict[str, int | None]:
    delta: dict[str, int | None] = {}
    for field in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"):
        left, right = value.get(field), baseline.get(field)
        delta[field] = None if left is None and right is None else int(left or 0) - int(right or 0)
    return delta


def _recovery_latency(events: list[dict[str, Any]]) -> float:
    return sum(
        float(event.get("duration_ms") or 0)
        for event in events
        if event["type"] == "model_error"
        or (event["type"] == "tool_call" and str((event.get("payload") or {}).get("origin", "")).startswith("recovery"))
    )


def run_reliability_evaluation(
    benchmark_root: Path,
    output_directory: Path,
    *,
    profile_path: Path | None = None,
    schedule_path: Path | None = None,
) -> dict[str, Any]:
    profile = load_evaluation_profile(profile_path or DEFAULT_RELIABILITY_PROFILE)
    if profile.resolved["track"] != "reliability":
        raise ValueError("reliability evaluation requires a reliability-track profile")
    schedule = load_fault_schedule(schedule_path or DEFAULT_FAULT_SCHEDULE)
    taxonomy = load_failure_taxonomy()
    cases = {case.case_id: case for case in load_cases(benchmark_root)}
    missing = sorted({scenario.case_id for scenario in schedule.scenarios} - set(cases))
    if missing:
        raise ValueError(f"fault schedule references unknown case: {missing[0]}")
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    policy = RecoveryPolicy.from_mapping(profile.resolved["recovery_policy"])

    baselines: dict[str, dict[str, Any]] = {}
    for case_id in sorted({scenario.case_id for scenario in schedule.scenarios}):
        case = cases[case_id]
        result, workspace = run_agent(
            RunConfig(case.repository, case.issue, output_directory / "baseline-runs", case.test_command, profile.run_limits, evaluation_profile=profile.artifact()),
            case.scripted_model(),
            run_id=case_id,
        )
        hidden = _hidden(case, workspace, output_directory / "baseline-hidden" / case_id)
        baselines[case_id] = {
            "result": result,
            "public_tests": _test_result(result.final_test),
            "hidden_tests": _test_result(hidden),
            "trace": summarize_trace(Path(result.trajectory_path)),
        }

    scenario_results: list[dict[str, Any]] = []
    for scenario in schedule.scenarios:
        case = cases[scenario.case_id]
        model = case.scripted_model().__class__(_actions(case, scenario), name=f"reliability-{scenario.scenario_id}")
        config = RunConfig(
            case.repository,
            case.issue,
            output_directory / "faulted-runs",
            case.test_command,
            profile.run_limits,
            evaluation_profile={**profile.artifact(), "fault_injection": True, "fault_schedule": schedule.artifact(), "scenario_id": scenario.scenario_id},
        )
        started = time.perf_counter()
        session = RunSession.start(config, run_id=scenario.scenario_id, model_metadata=model.metadata, transport="direct_fault_injected")
        runtime = FaultRuntime(scenario, session.recorder)
        faulted_model = FaultInjectingModel(model, runtime)
        faulted_tools = FaultInjectingBackend(session.tools, runtime)
        try:
            result = AgentLoop(
                issue=case.issue,
                model=faulted_model,
                tools=faulted_tools,
                recorder=session.recorder,
                max_iterations=config.limits.max_iterations,
                max_repair_cycles=config.limits.max_repair_cycles,
                total_timeout_seconds=config.limits.total_timeout_seconds,
                recovery_policy=policy,
                fault_runtime=runtime,
            ).run(scenario.scenario_id, started_at=started)
        finally:
            session.close()
        run_artifact = _write_run_artifact(session, result, profile)
        hidden_raw = _hidden(case, session.workspace, output_directory / "faulted-hidden" / scenario.scenario_id)
        public = _test_result(result.final_test)
        hidden = _test_result(hidden_raw)
        events = list(TraceReader(session.trajectory_path))
        failure_analysis = classify_failures(
            events,
            evaluation={
                "evidence_event_id": f"evaluation:{scenario.scenario_id}",
                "public_tests": public,
                "hidden_tests": hidden,
                "changed_files": result.changed_files,
                "allowed_fix_sets": [list(paths) for paths in case.allowed_fix_sets],
                "harness_failed": hidden["harness_failed"],
            },
            taxonomy=taxonomy,
        )
        baseline = baselines[scenario.case_id]
        observed_revisions = run_artifact["trace_summary"]["workspace_revisions"]["observed"]
        monotonic = observed_revisions == sorted(set(observed_revisions))
        expected_revision = 1 if result.changed_files else 0
        final_revision = run_artifact["trace_summary"]["workspace_revisions"]["final"] or 0
        duplicate_mutations = max(0, runtime.mutation_executions - 1)
        revision_divergence = int(not monotonic or final_revision != expected_revision)
        decisions = failure_analysis["recovery_decisions"]
        unsafe_retries = sum(bool(decision.get("unsafe")) for decision in decisions)
        budget_overshoots = sum(
            decision.get("action") in {"retry_model", "retry_read_only", "rerun_tests"}
            and (int(decision.get("remaining_retry_budget", 0)) <= 0 or float(decision.get("remaining_deadline_ms", 0)) <= 0)
            for decision in decisions
        )
        injected_labels = sorted(
            item["label"] for item in failure_analysis["classifications"] if item["fault_origin"] == "injected"
        )
        natural_labels = sorted(
            item["label"] for item in failure_analysis["classifications"] if item["fault_origin"] == "natural"
        )
        expected_labels = sorted(scenario.expected["taxonomy_labels"])
        expected_match = {
            "stop_reason": result.stop_reason == scenario.expected["stop_reason"],
            "public_pass": public["passed"] is scenario.expected["public_pass"],
            "hidden_pass": hidden["passed"] is scenario.expected["hidden_pass"],
            "taxonomy_labels": injected_labels == expected_labels,
        }
        scenario_result = redact_value({
            "scenario_id": scenario.scenario_id,
            "case_id": scenario.case_id,
            "fault_injection": True,
            "faults": [asdict(fault) for fault in scenario.faults],
            "expected": scenario.expected,
            "observed": {
                "stop_reason": result.stop_reason,
                "task_success": bool(public["passed"] and hidden["passed"]),
                "public_tests": public,
                "hidden_tests": hidden,
                "final_diff": result.final_diff,
                "changed_files": result.changed_files,
                "workspace_revision": final_revision,
                "taxonomy_labels": injected_labels,
                "natural_taxonomy_labels": natural_labels,
            },
            "expected_match": expected_match,
            "passed": all(expected_match.values()) and not any((unsafe_retries, duplicate_mutations, revision_divergence, budget_overshoots)),
            "failure_analysis": failure_analysis,
            "recovery": {
                "attempts": len(decisions),
                "automatic_attempts": sum(decision["action"] in {"retry_model", "retry_read_only", "rerun_tests", "reconcile_mutation"} for decision in decisions),
                "latency_ms": _recovery_latency(events),
                "decisions": decisions,
            },
            "safety": {
                "unsafe_retry_count": unsafe_retries,
                "duplicate_mutation_count": duplicate_mutations,
                "revision_divergence_count": revision_divergence,
                "budget_overshoot_count": budget_overshoots,
            },
            "baseline_comparison": {
                "additional_tool_calls": result.tool_calls - baseline["result"].tool_calls,
                "additional_latency_ms": result.latency_ms - baseline["result"].latency_ms,
                "additional_token_usage": _token_delta(asdict(result.usage), asdict(baseline["result"].usage)),
                "same_final_diff": result.final_diff == baseline["result"].final_diff,
                "baseline_public_pass": baseline["public_tests"]["passed"],
                "baseline_hidden_pass": baseline["hidden_tests"]["passed"],
                "baseline_workspace_revision": baseline["trace"]["workspace_revisions"]["final"],
            },
            "trace_path": result.trajectory_path,
            "injected_sequence": runtime.injected_sequence,
        })
        scenario_results.append(scenario_result)
        (session.run_directory / "reliability.json").write_text(json.dumps(scenario_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    safety_fields = ("unsafe_retry_count", "duplicate_mutation_count", "revision_divergence_count", "budget_overshoot_count")
    fault_totals: Counter[str] = Counter()
    fault_successes: Counter[str] = Counter()
    for scenario, result in zip(schedule.scenarios, scenario_results, strict=True):
        for fault in scenario.faults:
            fault_totals[fault.fault_type] += 1
            fault_successes[fault.fault_type] += bool(result["observed"]["task_success"] and scenario.expected["recoverable"])
    aggregate: dict[str, Any] = {
        "scenarios": len(scenario_results),
        "scenarios_passed": sum(bool(result["passed"]) for result in scenario_results),
        "recoverable_scenarios": sum(bool(scenario.expected["recoverable"]) for scenario in schedule.scenarios),
        "recoverable_scenarios_succeeded": sum(
            bool(scenario.expected["recoverable"] and result["observed"]["task_success"])
            for scenario, result in zip(schedule.scenarios, scenario_results, strict=True)
        ),
        "recovery_success_rate_by_fault_type": {
            fault: fault_successes[fault] / total for fault, total in sorted(fault_totals.items())
        },
        "unclassified_error_count": sum(result["failure_analysis"]["unclassified_error_count"] for result in scenario_results),
    }
    for field in safety_fields:
        aggregate[field] = sum(int(result["safety"][field]) for result in scenario_results)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_track": "deterministic_reliability",
        "fault_injection": True,
        "profile": profile.artifact(),
        "fault_schedule": schedule.artifact(),
        "taxonomy": taxonomy.artifact(),
        "baselines": {
            case_id: {
                "public_tests": value["public_tests"],
                "hidden_tests": value["hidden_tests"],
                "tool_calls": value["result"].tool_calls,
                "latency_ms": value["result"].latency_ms,
                "token_usage": asdict(value["result"].usage),
                "final_diff": value["result"].final_diff,
                "workspace_revision": value["trace"]["workspace_revisions"]["final"],
            }
            for case_id, value in baselines.items()
        },
        "scenarios": scenario_results,
        "aggregate": aggregate,
    }
    report["profile_acceptance"] = evaluate_profile_acceptance(profile, aggregate)
    json_path = output_directory / "reliability.json"
    markdown_path = output_directory / "reliability.md"
    json_path.write_text(json.dumps(redact_value(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# RepoPilot deterministic reliability evaluation", "",
        f"- Fault injection: true", f"- Schedule: {schedule.schedule_id} v{schedule.version}",
        f"- Schedule hash: {schedule.content_hash}",
        f"- Scenarios: {aggregate['scenarios_passed']}/{aggregate['scenarios']}",
        f"- Unsafe retries: {aggregate['unsafe_retry_count']}",
        f"- Duplicate mutations: {aggregate['duplicate_mutation_count']}",
        f"- Revision divergence: {aggregate['revision_divergence_count']}",
        f"- Budget overshoots: {aggregate['budget_overshoot_count']}", "",
        "| Scenario | Expected stop | Observed stop | Public | Hidden | Injected labels | Natural labels | Extra calls | Safety |",
        "|---|---|---|---:|---:|---|---|---:|---|",
    ]
    for result in scenario_results:
        safety = result["safety"]
        lines.append(
            f"| {result['scenario_id']} | {result['expected']['stop_reason']} | {result['observed']['stop_reason']} | "
            f"{result['observed']['public_tests']['passed']} | {result['observed']['hidden_tests']['passed']} | "
            f"{', '.join(result['observed']['taxonomy_labels'])} | "
            f"{', '.join(result['observed']['natural_taxonomy_labels']) or 'none'} | "
            f"{result['baseline_comparison']['additional_tool_calls']} | "
            f"u={safety['unsafe_retry_count']},d={safety['duplicate_mutation_count']},r={safety['revision_divergence_count']},b={safety['budget_overshoot_count']} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["report_paths"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report
