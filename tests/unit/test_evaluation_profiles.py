from __future__ import annotations

import json
from pathlib import Path

import pytest

from repopilot.evaluation.profiles import EvaluationProfileError, load_evaluation_profile, resolve_profile


def test_default_profile_is_fully_resolved_and_stable() -> None:
    first = load_evaluation_profile()
    second = load_evaluation_profile()
    assert first.artifact() == second.artifact()
    assert set(first.resolved["limits"]) == {
        "max_iterations", "max_repair_cycles", "command_timeout_seconds", "total_timeout_seconds",
        "max_observation_chars", "max_patch_chars",
    }
    assert first.content_hash.startswith("sha256:")


@pytest.mark.parametrize(
    "mutation",
    [
        {"unknown": True},
        {"limits": {"max_iterations": 0}},
        {"model_repetitions": 2},
        {"taxonomy": {"taxonomy_id": "pkg:callable", "version": 1}},
        {"acceptance_thresholds": {"localization_f1_mean_min": 1.1}},
    ],
)
def test_invalid_or_executable_profile_shapes_fail_closed(mutation: dict[str, object]) -> None:
    raw = json.loads(Path("configs/evaluation/default.json").read_text())
    raw.update(mutation)
    with pytest.raises(EvaluationProfileError):
        resolve_profile(raw)


def test_duplicate_json_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(EvaluationProfileError, match="duplicate"):
        load_evaluation_profile(path)
