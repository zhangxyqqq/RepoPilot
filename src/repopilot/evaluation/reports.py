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
    lines = [
        "# RepoPilot evaluation",
        "",
        f"- Cases: {aggregate['cases']}",
        f"- Tasks succeeded: {aggregate['tasks_succeeded']}",
        f"- Success rate: {aggregate['success_rate']:.1%}",
        f"- Mean localization F1: {aggregate['localization_f1_mean']:.3f}",
        f"- Total tool calls: {aggregate['tool_calls_total']}",
        f"- Unnecessary tool calls: {aggregate['unnecessary_tool_calls_total']}",
        f"- Total latency: {aggregate['latency_ms_total']:.1f} ms",
        "",
        "## Cases",
        "",
        "| Case | Success | Tests | Localization F1 | Calls | Unnecessary | Iterations | Latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        tests = case["tests"]
        test_summary = f"{tests['passed']} passed / {tests['failed']} failed / {tests['errors']} errors"
        lines.append(
            f"| {case['id']} | {'yes' if case['task_success'] else 'no'} | {test_summary} | "
            f"{case['localization_f1']:.3f} | {case['tool_calls']} | {case['unnecessary_tool_calls']} | "
            f"{case['iterations']} | {case['latency_ms']:.1f} |"
        )
    lines.extend(["", "Token counts are reported when the model provider supplies them.", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path
