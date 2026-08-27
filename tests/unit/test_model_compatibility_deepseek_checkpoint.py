from __future__ import annotations

import hashlib
import json
from pathlib import Path


CHECKPOINT = Path("docs/checkpoints/MODEL_COMPATIBILITY_DEEPSEEK.json")
RAW_REPORT = Path("docs/checkpoints/MODEL_COMPATIBILITY_DEEPSEEK_RUNS/MODEL_COMPATIBILITY_GATE.json")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_deepseek_frozen_comparison_result_is_internally_consistent() -> None:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    raw = json.loads(RAW_REPORT.read_text(encoding="utf-8"))

    assert checkpoint["final_status"] == "NOT COMPATIBLE"
    assert raw["compatibility_decision"] == "NOT COMPATIBLE"
    assert checkpoint["frozen_benchmark"]["profile_content_hash"] == raw["profile"]["content_hash"]
    assert checkpoint["frozen_benchmark"]["corpus_content_hash"] == raw["profile"]["corpus_hash"]
    assert checkpoint["raw_artifacts"]["report_sha256"] == _sha256(RAW_REPORT)
    assert raw["provider"]["provider"] == "deepseek"
    assert raw["provider"]["model"] == "deepseek-v4-pro"

    aggregate = raw["aggregate"]
    assert aggregate["cases"] == 12
    assert aggregate["first_valid_tool_call_rate"] == 1.0
    assert aggregate["protocol_complete_success_rate"] == 9 / 12
    assert aggregate["plan_only_responses_before_first_tool"] == 0
    assert aggregate["plan_only_loop_rate"] == 0.0
    assert aggregate["total_timeout_rate"] == 0.0
    assert aggregate["reasoning_tokens"] is None
    assert sum(case["score"]["model_tool_calls"] for case in raw["cases"]) == 31
    assert all(case["trace_reconciliation"]["passed"] for case in raw["cases"])
    assert all(case["security"]["passed"] for case in raw["cases"])
    assert all(case["contamination"]["passed"] for case in raw["cases"])


def test_deepseek_artifacts_preserve_prior_frozen_results_and_exclude_credentials() -> None:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    artifacts = [CHECKPOINT, RAW_REPORT, *Path("docs/checkpoints/MODEL_COMPATIBILITY_DEEPSEEK_RUNS/runs").rglob("*")]
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in artifacts if path.is_file())

    assert "DEEPSEEK_API_KEY=" not in serialized
    assert checkpoint["security_and_integrity"]["credential_artifact_scan"] == "PASS"
    assert _sha256(Path("configs/evaluation/model_compatibility.json")) == checkpoint["frozen_benchmark"]["profile_file_sha256"]
    assert _sha256(Path("benchmarks/model_compatibility/cases.v1.json")) == checkpoint["frozen_benchmark"]["corpus_file_sha256"]
    assert _sha256(Path("docs/checkpoints/MODEL_COMPATIBILITY_GATE.json")) == "sha256:d0b96049f346ef453e1b16ed0b573f91a7fd7260a5e96f566e5a845c111cdea1"
    assert _sha256(Path("docs/checkpoints/MODEL_COMPATIBILITY_RUNS/MODEL_COMPATIBILITY_GATE.json")) == "sha256:6643e5c9e6ec65f8dd089f8ce7592be906542e9c045119e32456b2db401190bd"
    assert _sha256(Path("docs/checkpoints/P2_BEHAVIORAL_PILOT.json")) == "sha256:7b0f900630059323ca77449b9aacce76cb828dd39b4103c78386e74063c23fd6"
