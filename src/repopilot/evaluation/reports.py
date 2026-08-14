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
    lines.extend(["", "Token counts are reported when the model provider supplies them.", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path
