from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repopilot.sandbox.swebench import SWEbenchSandbox, scan_contamination
from repopilot.sandbox.test_plan import TrustedTestPlan
from repopilot.tools.definitions import PUBLIC_TOOL_DEFINITIONS
from repopilot.tools.registry import ToolRegistry


TRACK = "swebench_feasibility_security"
PUBLIC_TOOLS = tuple(definition.name for definition in PUBLIC_TOOL_DEFINITIONS)


def load_feasibility_profile(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version", "profile_id", "profile_version", "track", "dataset",
        "official_environment", "selection_frozen_at", "selection_frozen_before_environment_testing",
        "selection_policy", "candidates", "frozen_instance_ids", "runtime_security_gate",
        "model_visible_tools", "trusted_test_plans", "claim_boundary",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown SWE-bench feasibility profile fields: {sorted(unknown)}")
    if value.get("schema_version") != 1 or value.get("profile_version") != 1 or value.get("track") != TRACK:
        raise ValueError("unsupported SWE-bench feasibility profile version or track")
    frozen = value.get("frozen_instance_ids")
    if not isinstance(frozen, list) or len(frozen) != 3 or len(set(frozen)) != 3:
        raise ValueError("SWE-bench feasibility profile must freeze exactly three unique instances")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise ValueError("SWE-bench feasibility profile must retain all five candidates")
    selected = [item.get("instance_id") for item in candidates if item.get("selected") is True]
    if selected != frozen or any("pre_evaluation_reason" not in item for item in candidates):
        raise ValueError("frozen instances must equal the predeclared selected candidates in order")
    if tuple(value.get("model_visible_tools", ())) != PUBLIC_TOOLS:
        raise ValueError("SWE-bench feasibility model-visible tools must equal the canonical six-tool surface")
    plans = value.get("trusted_test_plans")
    if not isinstance(plans, list) or {plan.get("instance_id") for plan in plans} != set(frozen):
        raise ValueError("every frozen instance requires exactly one trusted test plan")
    resolved_plans = [TrustedTestPlan.from_dict(plan) for plan in plans]
    if len({plan.instance_id for plan in resolved_plans}) != 3:
        raise ValueError("trusted test plans must have unique instance IDs")
    value["resolved_test_plans"] = {plan.instance_id: plan for plan in resolved_plans}
    return value


def run_six_tool_smoke(
    sandbox: SWEbenchSandbox,
    *,
    search_query: str,
    read_path: str,
    patch_path: str,
    forbidden_names: tuple[str, ...] = (),
    forbidden_blobs: tuple[str, ...] = (),
) -> dict[str, Any]:
    registry = ToolRegistry(sandbox)  # type: ignore[arg-type]
    source = sandbox.workspace / patch_path
    lines = source.read_text(encoding="utf-8").splitlines()
    anchor = next(
        line for line in lines
        if line.strip() and not line.startswith((" ", "\t")) and lines.count(line) == 1
    )
    patch = "\n".join(
        [
            "*** Begin Patch", f"*** Update File: {patch_path}", "@@", f" {anchor}",
            "+# RepoPilot feasibility smoke; this worktree is discarded.", "*** End Patch",
        ]
    )
    results = {
        "list_files": registry.call("list_files", {}),
        "search_code": registry.call("search_code", {"query": search_query}),
        "read_file": registry.call("read_file", {"path": read_path, "start_line": 1, "end_line": 40}),
        "apply_patch": registry.call("apply_patch", {"patch": patch}),
        "run_tests": registry.call("run_tests", {}),
        "git_diff": registry.call("git_diff", {}),
    }
    details = {
        name: {
            "ok": result.ok, "latency_ms": result.latency_ms, "revision": result.revision, "error": result.error,
        }
        for name, result in results.items()
    }
    details["run_tests"]["tests_passed"] = results["run_tests"].observation.get("passed")
    details["git_diff"]["changed_files"] = results["git_diff"].observation.get("changed_files")
    protected_path = sandbox.test_plan.command[-1]
    protected_source = sandbox.workspace / protected_path
    protected_lines = protected_source.read_text(encoding="utf-8").splitlines()
    protected_anchor = next(line for line in protected_lines if line.strip() and protected_lines.count(line) == 1)
    protected_patch = "\n".join(
        [
            "*** Begin Patch", f"*** Update File: {protected_path}", "@@", f" {protected_anchor}",
            "+# forbidden model-authored test edit", "*** End Patch",
        ]
    )
    policy_checks = {
        "path_escape_rejected": registry.call("read_file", {"path": "../../etc/passwd"}).ok is False,
        "protected_test_edit_rejected": registry.call("apply_patch", {"patch": protected_patch}).ok is False,
        "model_selected_test_command_rejected": registry.call("run_tests", {"command": ["sh", "-c", "id"]}).ok is False,
    }
    contamination = scan_contamination(
        sandbox.workspace,
        visible_artifacts=[
            {"issue": sandbox.issue},
            {
                name: {"observation": result.observation, "error": result.error}
                for name, result in results.items()
            },
        ],
        forbidden_names=forbidden_names,
        forbidden_blobs=forbidden_blobs,
    )
    passed = (
        tuple(results) == PUBLIC_TOOLS
        and all(result.ok for result in results.values())
        and results["run_tests"].observation.get("passed") is True
        and results["git_diff"].observation.get("changed_files") == [patch_path]
        and all(policy_checks.values())
    )
    return {
        "passed": passed,
        "tools": details,
        "canonical_tool_names": list(PUBLIC_TOOLS),
        "policy_checks": policy_checks,
        "contamination": contamination,
    }


def write_feasibility_report(report: dict[str, Any], output_directory: Path) -> dict[str, Path]:
    if report.get("track") != TRACK:
        raise ValueError(f"feasibility report track must be {TRACK}")
    cases = report.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("feasibility report must contain exactly the frozen three cases")
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "swebench-feasibility.json"
    markdown_path = output_directory / "swebench-feasibility.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = [
        "# SWE-bench Verified feasibility and security spike", "",
        "> Environment/security evidence only. No live-agent SWE-bench solve was attempted.", "",
        "| Instance | Gold grading | Security | Contamination | Six tools | Decision |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        rows.append(
            f"| `{case['instance_id']}` | {case['gold_patch']['status']} | "
            f"{case['security']['status']} | {case['contamination']['status']} | "
            f"{case['tool_smoke']['status']} | **{case['decision']}** |"
        )
    aggregate = report["aggregate"]
    rows.extend(
        ["", f"Overall: **{aggregate['feasible_instances']}/{aggregate['frozen_instances']} feasible**.", "",
         f"Recommendation: **{aggregate['recommendation']}**."]
    )
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
