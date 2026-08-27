from __future__ import annotations

import json
from pathlib import Path

import pytest

from repopilot.evaluation.model_compatibility import (
    DECISIONS,
    SWE_BENCH_CONTAMINATED_IDS,
    _compatibility_model,
    _comparison_provider,
    corpus_hash,
    load_corpus,
    load_profile,
)
from repopilot.llm import ProviderConfig


CORPUS = Path("benchmarks/model_compatibility/cases.v1.json")
PROFILE = Path("configs/evaluation/model_compatibility.json")


def test_frozen_compatibility_corpus_has_twelve_protocol_cases_and_no_swebench_inputs() -> None:
    raw, cases = load_corpus(CORPUS)
    assert len(cases) == 12
    assert {case.category for case in cases} == {
        "tool_initiation", "tool_sequencing", "action_correction", "state_awareness", "termination",
    }
    assert all(case.repository.is_dir() for case in cases)
    serialized = json.dumps(raw).casefold()
    assert all(instance not in serialized for instance in SWE_BENCH_CONTAMINATED_IDS)
    assert raw["claim_boundary"].endswith("not a software-engineering benchmark.")


def test_profile_freezes_corpus_protocol_model_limits_and_gate() -> None:
    profile = load_profile(PROFILE)
    assert profile["corpus"]["content_hash"] == corpus_hash(CORPUS.parent)
    assert profile["provider"]["model"] == "mistral:7b"
    assert profile["provider"]["repetitions_per_case"] == 1
    assert profile["controller"]["total_timeout_seconds"] == 60
    assert profile["controller"]["max_iterations"] == 8
    assert profile["agent_protocol"]["model_visible_tools"] == [
        "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff",
    ]
    assert profile["agent_protocol"]["retrieval_strategy"] == "structural"
    assert tuple(profile["admission_gate"]["decision_values"]) == DECISIONS
    assert profile["admission_gate"]["first_valid_tool_call_rate_min"] == 0.9
    assert profile["admission_gate"]["protocol_complete_success_rate_min"] == 0.8
    assert profile["admission_gate"]["plan_only_timeout_rate_max"] == 0.0


@pytest.mark.parametrize("section", ["top", "provider", "controller", "gate"])
def test_profile_rejects_unknown_fields_before_runtime(tmp_path: Path, section: str) -> None:
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    target = {
        "top": value,
        "provider": value["provider"],
        "controller": value["controller"],
        "gate": value["admission_gate"],
    }[section]
    target["python_callable"] = "package.module:function"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or unknown"):
        load_profile(path)


def test_corpus_hash_detects_fixture_drift(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "case.json").write_text("{}\n", encoding="utf-8")
    before = corpus_hash(root)
    (root / "case.json").write_text('{"changed":true}\n', encoding="utf-8")
    assert corpus_hash(root) != before


def test_profile_preserves_no_hard_deadline_claim() -> None:
    profile = load_profile(PROFILE)
    assert profile["deadline_diagnostics"]["hard_cancellation_supported"] is False
    assert profile["deadline_diagnostics"]["measure_cleanup_completion"] is True
    assert profile["deadline_diagnostics"]["measure_artifact_completion"] is True


def test_deepseek_comparison_changes_only_execution_provider() -> None:
    profile = load_profile(PROFILE)
    before = {
        "content_hash": profile["content_hash"],
        "corpus_hash": profile["corpus"]["content_hash"],
        "controller": profile["controller"],
        "protocol": profile["agent_protocol"],
        "gate": profile["admission_gate"],
    }

    descriptor = _comparison_provider(
        profile,
        ProviderConfig(provider="deepseek", model="deepseek-v4-pro"),
    )

    assert descriptor == {
        "provider": "deepseek",
        "endpoint_owner": "official DeepSeek",
        "endpoint": "https://api.deepseek.com",
        "endpoint_api": "Chat Completions",
        "model": "deepseek-v4-pro",
        "thinking_mode": "disabled",
        "sdk_timeout_seconds": 60,
        "sdk_retry_limit": 0,
        "temperature": None,
        "random_seed": None,
        "repetitions_per_case": 1,
        "credentials_required": True,
    }
    assert before == {
        "content_hash": profile["content_hash"],
        "corpus_hash": profile["corpus"]["content_hash"],
        "controller": profile["controller"],
        "protocol": profile["agent_protocol"],
        "gate": profile["admission_gate"],
    }


def test_deepseek_comparison_model_metadata_never_contains_credential() -> None:
    profile = load_profile(PROFILE)
    credential = "deepseek-comparison-secret"
    model, descriptor = _compatibility_model(
        profile,
        ProviderConfig(provider="deepseek", model="deepseek-v4-pro"),
        environ={"DEEPSEEK_API_KEY": credential},
        client=object(),
    )

    assert model.metadata == {"provider": "deepseek", "model": "deepseek-v4-pro", "deterministic": False}
    assert credential not in json.dumps(model.metadata)
    assert credential not in json.dumps(descriptor)


@pytest.mark.parametrize(
    "provider,model",
    [("deepseek", "deepseek-v4-flash"), ("openai_compatible", "deepseek-v4-pro")],
)
def test_comparison_rejects_non_predeclared_provider_or_model(provider: str, model: str) -> None:
    profile = load_profile(PROFILE)
    config = ProviderConfig(
        provider=provider,
        model=model,
        base_url="http://127.0.0.1:8000/v1" if provider == "openai_compatible" else None,
    )
    with pytest.raises(ValueError, match="permits only"):
        _comparison_provider(profile, config)
