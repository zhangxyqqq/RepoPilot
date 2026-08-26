from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repopilot.evaluation.swebench_behavioral import (
    INSTANCE_IDS,
    load_behavioral_profile,
    stage_fresh_checkout,
    verify_frozen_local_model,
)


PROFILE = Path("configs/evaluation/swebench_verified_behavioral_pilot.json")


def test_behavioral_profile_freezes_exact_two_cases_and_all_boundaries() -> None:
    profile = load_behavioral_profile(PROFILE)
    assert tuple(item["instance_id"] for item in profile["instances"]) == INSTANCE_IDS
    assert profile["repetitions_per_instance"] == 1
    assert profile["retrieval"]["strategy"] == "structural"
    assert profile["retrieval"]["semantic_enabled"] is False
    assert profile["retrieval"]["hybrid_enabled"] is False
    assert profile["official_environment"]["harness_retry_limit"] == 0
    assert profile["provider"]["credentials_required"] is False
    assert profile["controller"]["max_iterations"] == 30
    assert profile["controller"]["max_repair_cycles"] == 3
    assert profile["controller"]["total_timeout_seconds"] == 300
    assert profile["protocol"]["run_order"] == list(INSTANCE_IDS)
    assert profile["content_hash"].startswith("sha256:")


@pytest.mark.parametrize("location", ["top", "provider", "sandbox", "instance"])
def test_behavioral_profile_rejects_unknown_fields_before_runtime(tmp_path: Path, location: str) -> None:
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    target = {
        "top": value,
        "provider": value["provider"],
        "sandbox": value["sandbox"],
        "instance": value["instances"][0],
    }[location]
    target["python_callable"] = "package.module:function"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        load_behavioral_profile(path)


def test_behavioral_profile_rejects_tool_retrieval_budget_and_security_drift(tmp_path: Path) -> None:
    mutations = (
        lambda value: value["agent"]["model_visible_tools"].append("shell"),
        lambda value: value["retrieval"].update({"strategy": "hybrid", "hybrid_enabled": True}),
        lambda value: value["controller"].update({"max_iterations": 31}),
        lambda value: value["sandbox"].update({"network_mode": "bridge"}),
    )
    for index, mutate in enumerate(mutations):
        value = json.loads(PROFILE.read_text(encoding="utf-8"))
        mutate(value)
        path = tmp_path / f"invalid-{index}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            load_behavioral_profile(path)


def test_fresh_staging_is_exact_and_refuses_reuse(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "fixture@example.com"], check=True)
    (source / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "source.py"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
    ).stdout.strip()
    (source / "source.py").write_text("VALUE = 2\n", encoding="utf-8")

    target = tmp_path / "target"
    stage_fresh_checkout(source, target, commit)
    assert (target / "source.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    with pytest.raises(FileExistsError, match="already exists"):
        stage_fresh_checkout(source, target, commit)


def test_frozen_model_verification_is_read_only_and_checks_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = load_behavioral_profile(PROFILE)
    manifest = profile["provider"]["model_manifest_sha256"].removeprefix("sha256:")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command == ["ollama", "list"]
        return subprocess.CompletedProcess(command, 0, f"NAME ID SIZE\nmistral:7b {manifest[:12]} 4.4 GB\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert verify_frozen_local_model(profile)["passed"] is True


def test_profile_contains_no_oracle_or_solution_fields() -> None:
    text = PROFILE.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "reference_patch", "test_patch", "expected_fix_files", "fail_to_pass", "pass_to_pass",
        "gold_prediction", "solution_patch",
    ):
        assert forbidden not in text
