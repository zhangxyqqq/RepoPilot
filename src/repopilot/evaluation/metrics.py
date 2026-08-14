from __future__ import annotations

import re
from typing import Any, Iterable


def localization_metrics(
    changed_files: Iterable[str],
    allowed_fix_sets: Iterable[Iterable[str]],
) -> dict[str, float]:
    changed = set(changed_files)
    candidates = [set(paths) for paths in allowed_fix_sets]
    if not candidates:
        candidates = [set()]
    best: dict[str, float] | None = None
    for expected in candidates:
        overlap = len(changed & expected)
        precision = overlap / len(changed) if changed else (1.0 if not expected else 0.0)
        recall = overlap / len(expected) if expected else (1.0 if not changed else 0.0)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        candidate = {"precision": precision, "recall": recall, "f1": f1}
        if best is None or candidate["f1"] > best["f1"]:
            best = candidate
    assert best is not None
    return best


def parse_pytest_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0}
    patterns = {"passed": r"(\d+) passed", "failed": r"(\d+) failed", "errors": r"(\d+) errors?"}
    for key, pattern in patterns.items():
        matches = re.findall(pattern, output)
        if matches:
            counts[key] = int(matches[-1])
    return counts


def aggregate(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(case_results)
    successful = sum(bool(case["task_success"]) for case in case_results)

    def average(key: str) -> float:
        return sum(float(case[key]) for case in case_results) / total if total else 0.0

    token_fields = ["input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"]
    tokens: dict[str, int | None] = {}
    for field in token_fields:
        values = [case["token_usage"].get(field) for case in case_results]
        tokens[field] = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
    return {
        "cases": total,
        "tasks_succeeded": successful,
        "success_rate": successful / total if total else 0.0,
        "public_test_cases_passed": sum(bool(case["public_tests"]["passed"]) for case in case_results),
        "hidden_test_cases_passed": sum(bool(case["hidden_tests"]["passed"]) for case in case_results),
        "localization_f1_mean": average("localization_f1"),
        "tool_calls_total": sum(case["tool_calls"] for case in case_results),
        "unnecessary_tool_calls_total": sum(case["unnecessary_tool_calls"] for case in case_results),
        "iterations_mean": average("iterations"),
        "repair_cycles_total": sum(case["repair_cycles"] for case in case_results),
        "latency_ms_total": sum(case["latency_ms"] for case in case_results),
        "token_usage": tokens,
    }
