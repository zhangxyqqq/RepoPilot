from __future__ import annotations

from pathlib import Path

from repopilot.evaluation.cases import load_cases
from repopilot.retrieval import RetrievalConfig, select_context


def _file_rank(result, expected: set[str]) -> int | None:
    ranked = list(dict.fromkeys(candidate.path for candidate in result.candidates))
    matches = [index for index, path in enumerate(ranked, 1) if path in expected]
    return min(matches) if matches else None


def test_structural_and_hybrid_recall_at_5_remain_perfect_on_controlled_cases(monkeypatch) -> None:
    monkeypatch.setenv("REPOPILOT_RETRIEVAL_CACHE_NAMESPACE", "controlled-nonregression")
    for strategy in ("structural", "hybrid"):
        hits = 0
        for case in load_cases(Path("benchmarks/cases")):
            config = RetrievalConfig(strategy=strategy, max_files=5, max_chunks=80)
            result = select_context(case.repository, case.repository, case.issue, config)
            rank = _file_rank(result, set(case.expected_fix_files))
            hits += bool(rank and rank <= 5)
        assert hits == 12, strategy
