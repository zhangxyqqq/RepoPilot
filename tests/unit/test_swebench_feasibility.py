from __future__ import annotations

import json
from pathlib import Path

import pytest

from repopilot.evaluation.swebench_feasibility import (
    PUBLIC_TOOLS,
    load_feasibility_profile,
    write_feasibility_report,
)
from repopilot.sandbox.swebench import SWEbenchSandbox, scan_contamination
from repopilot.sandbox.test_plan import TrustedTestPlan


PROFILE = Path("configs/evaluation/swebench_verified_feasibility.json")


def test_frozen_profile_has_exact_candidates_tools_images_and_data_only_plans() -> None:
    profile = load_feasibility_profile(PROFILE)
    assert profile["frozen_instance_ids"] == [
        "pallets__flask-5014",
        "psf__requests-5414",
        "pytest-dev__pytest-10051",
    ]
    assert len(profile["candidates"]) == 5
    assert tuple(profile["model_visible_tools"]) == PUBLIC_TOOLS
    assert profile["runtime_security_gate"]["network_mode"] == "none"
    assert all("@sha256:" in image for image in profile["official_environment"]["images"].values())
    assert set(profile["resolved_test_plans"]) == set(profile["frozen_instance_ids"])
    assert all(plan.content_hash.startswith("sha256:") for plan in profile["resolved_test_plans"].values())


@pytest.mark.parametrize(
    "command",
    [
        ["sh", "-c", "pytest"],
        ["python", "-m", "pytest", "-q", "../tests/test_x.py"],
        ["python", "-m", "pytest", "-q", "tests/test_x.py", "--collect-only"],
        ["python", "-m", "pytest", "-q", "src/module.py"],
        ["python", "-m", "pytest", "-q", "src/test_module.py"],
        ["python", "-m", "pytest", "-q", "tests/conftest.py"],
    ],
)
def test_trusted_test_plan_rejects_shell_escape_and_broadening(command: list[str]) -> None:
    with pytest.raises(ValueError):
        TrustedTestPlan.from_dict(
            {"plan_id": "test", "version": 1, "instance_id": "x", "command": command, "source": "fixture"}
        )


@pytest.mark.parametrize(
    "target",
    ["testing/acceptance_test.py", "test_requests.py", "lib/matplotlib/tests/test_afm.py"],
)
def test_trusted_test_plan_accepts_frozen_cohort_test_shapes(target: str) -> None:
    plan = TrustedTestPlan.from_dict(
        {
            "plan_id": "test",
            "version": 1,
            "instance_id": "x",
            "command": ["python", "-m", "pytest", "-q", target],
            "source": "controller-owned deterministic path selection",
        }
    )
    assert plan.command[-1] == target


def test_profile_unknown_field_fails_before_runtime(tmp_path: Path) -> None:
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    value["python_callable"] = "package.module:function"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        load_feasibility_profile(path)


def test_mutable_image_and_mismatched_plan_are_rejected_before_docker(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    plan = TrustedTestPlan.from_dict(
        {
            "plan_id": "test", "version": 1, "instance_id": "x",
            "command": ["python", "-m", "pytest", "-q", "tests/test_x.py"], "source": "fixture",
        }
    )
    with pytest.raises(ValueError, match="pinned"):
        SWEbenchSandbox(
            workspace,
            instance_id="x",
            image="official:latest",
            expected_base_commit="a" * 40,
            test_plan=plan,
        )
    with pytest.raises(ValueError, match="does not match"):
        SWEbenchSandbox(
            workspace,
            instance_id="y",
            image="official@sha256:" + "a" * 64,
            expected_base_commit="a" * 40,
            test_plan=plan,
        )


def test_contamination_scan_checks_workspace_and_visible_outputs(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("SAFE = True\n", encoding="utf-8")
    clean = scan_contamination(
        tmp_path,
        visible_artifacts=[{"prompt": "issue only"}],
        forbidden_names=["reference.patch", "test.patch"],
        forbidden_blobs=["secret gold patch"],
    )
    assert clean == {"passed": True, "forbidden_name_hits": [], "forbidden_blob_hash_hits": []}
    (tmp_path / "reference.patch").write_text("secret gold patch\n", encoding="utf-8")
    contaminated = scan_contamination(
        tmp_path,
        forbidden_names=["reference.patch"],
        forbidden_blobs=["secret gold patch"],
    )
    assert contaminated["passed"] is False
    assert contaminated["forbidden_name_hits"] == ["reference.patch"]
    assert len(contaminated["forbidden_blob_hash_hits"]) == 1


def test_feasibility_report_keeps_claim_boundary_and_is_deterministic(tmp_path: Path) -> None:
    cases = [
        {
            "instance_id": instance,
            "gold_patch": {"status": "PASS"},
            "security": {"status": "PASS"},
            "contamination": {"status": "PASS"},
            "tool_smoke": {"status": "PASS"},
            "decision": "PASS",
        }
        for instance in ("a", "b", "c")
    ]
    report = {
        "track": "swebench_feasibility_security",
        "live_agent_behavior_run": False,
        "cases": cases,
        "aggregate": {"frozen_instances": 3, "feasible_instances": 3, "recommendation": "ELIGIBLE FOR BEHAVIORAL PILOT"},
    }
    paths = write_feasibility_report(report, tmp_path)
    first = paths["json"].read_bytes()
    write_feasibility_report(report, tmp_path)
    assert paths["json"].read_bytes() == first
    assert "No live-agent SWE-bench solve was attempted" in paths["markdown"].read_text(encoding="utf-8")
