from __future__ import annotations

import json
from pathlib import Path

import pytest

from repopilot.evaluation.retrieval import (
    RetrievalBenchmarkError,
    evaluate_retrieval,
    load_retrieval_corpus,
)


CORPUS = Path("benchmarks/retrieval/cases.v1.json")
PROFILE = Path("configs/evaluation/retrieval.json")


def test_challenge_corpus_has_frozen_development_and_held_out_splits() -> None:
    corpus = load_retrieval_corpus(CORPUS)
    assert len(corpus.cases) == 8
    assert corpus.artifact()["split_counts"] == {"dev": 3, "held_out": 5}
    assert sum(case.semantic_help_expected for case in corpus.cases) >= 3
    assert corpus.content_hash.startswith("sha256:")


def test_structural_evaluation_is_rank_deterministic_and_oracle_safe(tmp_path: Path) -> None:
    first = evaluate_retrieval(CORPUS, PROFILE, tmp_path / "first", strategies=("structural",))
    second = evaluate_retrieval(CORPUS, PROFILE, tmp_path / "second", strategies=("structural",))
    assert first["deterministic_fingerprint"] == second["deterministic_fingerprint"]
    assert first["oracle_leakage_check"] == {"passed": True, "staged_oracle_fields": 0}
    assert first["strategies"]["structural"]["aggregate"]["recall_at_5"] == 1.0
    for workspace in (tmp_path / "first" / "workspaces").glob("*/repository"):
        assert not any(path.name in {"case.json", "oracle.json"} for path in workspace.rglob("*"))


def test_invalid_oracle_path_fails_before_evaluation(tmp_path: Path) -> None:
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    raw["cases"][0]["expected_fix_files"] = ["missing.py"]
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RetrievalBenchmarkError, match="oracle file missing"):
        load_retrieval_corpus(invalid)
