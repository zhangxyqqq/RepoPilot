from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_reports(report: dict[str, Any], output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "evaluation.json"
    markdown_path = output_directory / "evaluation.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    aggregate = report["aggregate"]
    models = sorted({case["model"]["model"] for case in report["cases"]})
    lines = [
        f"# RepoPilot {report['evaluation_mode']} evaluation",
        "",
        f"- Evaluation track: {report.get('evaluation_track', 'legacy/unavailable')}",
        f"- Profile: {report.get('profile', {}).get('profile_id', 'unavailable')} v{report.get('profile', {}).get('profile_version', 'unavailable')}",
        f"- Profile hash: {report.get('profile', {}).get('content_hash', 'unavailable')}",
        f"- Taxonomy: {report.get('taxonomy', {}).get('taxonomy_id', 'unavailable')} v{report.get('taxonomy', {}).get('version', 'unavailable')}",
        f"- Model: {', '.join(models)}",
        f"- Cases: {aggregate['cases']}",
        f"- Tasks succeeded: {aggregate['tasks_succeeded']}",
        f"- Success rate: {aggregate['success_rate']:.1%}",
        f"- Public test cases passed: {aggregate['public_test_cases_passed']}",
        f"- Hidden test cases passed: {aggregate['hidden_test_cases_passed']}",
        f"- Mean localization F1: {aggregate['localization_f1_mean']:.3f}",
        f"- Total tool calls: {aggregate['tool_calls_total']}",
        f"- Unnecessary tool calls: {aggregate['unnecessary_tool_calls_total']}",
        f"- Total latency: {aggregate['latency_ms_total']:.1f} ms",
        f"- Profile acceptance: {'PASS' if report.get('profile_acceptance', {}).get('passed') else 'FAIL'}",
        "",
        "## Cases",
        "",
        "| Case | Success | Public | Hidden | Loc. P/R/F1 | Calls | Unnecessary | Iterations | Repairs | Stop | Latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for case in report["cases"]:
        public = case["public_tests"]
        hidden = case["hidden_tests"]
        public_counts = public["counts"]
        hidden_counts = hidden["counts"]
        public_summary = f"{public_counts['passed']}p/{public_counts['failed']}f/{public_counts['errors']}e"
        hidden_summary = f"{hidden_counts['passed']}p/{hidden_counts['failed']}f/{hidden_counts['errors']}e"
        localization = (
            f"{case['localization_precision']:.2f}/{case['localization_recall']:.2f}/"
            f"{case['localization_f1']:.2f}"
        )
        lines.append(
            f"| {case['id']} | {'yes' if case['task_success'] else 'no'} | {public_summary} | {hidden_summary} | "
            f"{localization} | {case['tool_calls']} | {case['unnecessary_tool_calls']} | "
            f"{case['iterations']} | {case['repair_cycles']} | {case['stop_reason']} | {case['latency_ms']:.1f} |"
        )
    failure = aggregate.get("failure_analysis", {})
    lines.extend([
        "",
        "## Failure analysis",
        "",
        f"- Agent behavioral failure cases: {failure.get('agent_behavioral_failure_cases', 0)}",
        f"- Infrastructure/harness failure cases: {failure.get('infrastructure_harness_failure_cases', 0)}",
        f"- Recovered failure cases: {failure.get('recovered_failure_cases', 0)}",
        f"- Efficiency-only degradation cases: {failure.get('efficiency_only_degradation_cases', 0)}",
        f"- Public-pass/hidden-fail cases: {failure.get('public_pass_hidden_fail_count', 0)}",
        f"- Unclassified structured errors: {failure.get('unclassified_error_count', 0)}",
        "",
        "| Label | Incidence | Stage | Recovered/unrecovered | Evidence event IDs |",
        "|---|---:|---|---|---|",
    ])
    incidence = failure.get("failure_incidence_by_label", {})
    for label in sorted(incidence):
        case_entries: list[str] = []
        stages: set[str] = set()
        recovery: set[str] = set()
        for case in report["cases"]:
            matches = [item for item in case.get("failure_analysis", {}).get("classifications", []) if item["label"] == label]
            for item in matches:
                stages.add(item["phase"])
                recovery.add(item["recoverability"])
                case_entries.append(f"{case['id']}:" + ",".join(item["evidence_event_ids"]))
        lines.append(f"| {label} | {incidence[label]} | {', '.join(sorted(stages))} | {', '.join(sorted(recovery))} | {'; '.join(case_entries)} |")
    if not incidence:
        lines.append("| none | 0 | — | — | — |")
    lines.extend(["", "Observed classifications are evidence-linked; manual hypotheses are kept separate and are empty unless explicitly supplied.", "", "Token counts are reported when the model provider supplies them.", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path
