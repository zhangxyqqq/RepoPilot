from __future__ import annotations

import hashlib
import json
from pathlib import Path


CHECKPOINT = Path("docs/checkpoints/MODEL_COMPATIBILITY_GATE.json")
RAW_REPORT = Path("docs/checkpoints/MODEL_COMPATIBILITY_RUNS/MODEL_COMPATIBILITY_GATE.json")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_compatibility_result_is_internally_consistent() -> None:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    raw = json.loads(RAW_REPORT.read_text(encoding="utf-8"))

    assert checkpoint["final_status"] == "NOT COMPATIBLE"
    assert raw["compatibility_decision"] == "NOT COMPATIBLE"
    assert checkpoint["frozen_configuration"]["profile_content_hash"] == raw["profile"]["content_hash"]
    assert checkpoint["frozen_configuration"]["corpus_content_hash"] == raw["profile"]["corpus_hash"]
    assert checkpoint["raw_artifacts"]["report_sha256"] == _sha256(RAW_REPORT)

    aggregate = raw["aggregate"]
    assert aggregate["cases"] == 12
    assert aggregate["first_valid_tool_call_rate"] == 0.0
    assert aggregate["protocol_complete_success_rate"] == 0.0
    assert aggregate["plan_only_responses_before_first_tool"] == 43
    assert aggregate["plan_only_loop_rate"] == 10 / 12
    assert aggregate["premature_final_rate"] == 2 / 12
    assert sum(case["score"]["model_tool_calls"] for case in raw["cases"]) == 0
    assert all(case["trace_reconciliation"]["passed"] for case in raw["cases"])
    assert all(case["security"]["passed"] for case in raw["cases"])
    assert all(case["contamination"]["passed"] for case in raw["cases"])
    assert sum(case["score"]["failure_analysis"]["unclassified_error_count"] for case in raw["cases"]) == 0


def test_prior_behavioral_pilot_remains_byte_identical() -> None:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    preserved = checkpoint["preserved_behavioral_pilot"]

    assert _sha256(Path("docs/checkpoints/P2_BEHAVIORAL_PILOT.json")) == preserved["report_sha256"]
    assert _sha256(Path("configs/evaluation/swebench_verified_behavioral_pilot.json")) == preserved["profile_sha256"]
    assert preserved["result"] == "0/2 resolved"
    assert preserved["rerun"] is False
    assert preserved["modified"] is False
