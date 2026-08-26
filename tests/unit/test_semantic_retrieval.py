from __future__ import annotations

from pathlib import Path

from repopilot.evaluation.retrieval import load_retrieval_corpus, materialize_case
from repopilot.retrieval import HybridSelector, RetrievalConfig, SemanticSelector, StructuralSelector
from repopilot.retrieval.chunking import chunk_repository


CORPUS = Path("benchmarks/retrieval/cases.v1.json")


def test_ast_chunking_is_deterministic_and_bounded(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    path = repository / "src" / "module.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import os\n\nclass Service:\n    def run(self):\n        return True\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    config = RetrievalConfig(max_chunks=3)
    first = chunk_repository(repository, repository, config)
    second = chunk_repository(repository, repository, config)
    assert first == second
    assert len(first) == 3
    assert [candidate.symbol for candidate in first] == ["<module>", "Service", "Service.run"]
    assert len({candidate.candidate_id for candidate in first}) == 3


def test_local_semantic_selector_recovers_development_paraphrase_and_warms_cache(tmp_path: Path, monkeypatch) -> None:
    case = load_retrieval_corpus(CORPUS).cases[0]
    repository = materialize_case(case, tmp_path)
    monkeypatch.setenv("REPOPILOT_RETRIEVAL_CACHE_NAMESPACE", "unit-semantic-paraphrase")
    config = RetrievalConfig(strategy="semantic", max_files=5, max_chunks=40)
    first = SemanticSelector().select(repository, repository, case.issue, config)
    second = SemanticSelector().select(repository, repository, case.issue, config)
    ranked_files = list(dict.fromkeys(candidate.path for candidate in first.candidates))

    assert ranked_files.index(case.expected_fix_files[0]) + 1 <= 3
    assert first.cache["persistent_service"] is False
    assert second.cache["hits"] > 0
    assert not any("retrieval-cache" in str(path) for path in repository.rglob("*"))


def test_hybrid_uses_rrf_and_reports_semantic_fallback(tmp_path: Path) -> None:
    case = load_retrieval_corpus(CORPUS).cases[1]
    repository = materialize_case(case, tmp_path)
    config = RetrievalConfig(strategy="hybrid")
    normal = HybridSelector().select(repository, repository, case.issue, config)
    fallback = HybridSelector(expected_model_sha256="0" * 64).select(repository, repository, case.issue, config)

    assert normal.fallback is None
    assert all(candidate.fused_rank is not None for candidate in normal.candidates)
    assert fallback.strategy == "hybrid"
    assert fallback.fallback == {
        "used": True,
        "from_strategy": "hybrid",
        "to_strategy": "structural_lexical",
        "error_code": "semantic_initialization_failed",
        "message": "local semantic model weight hash mismatch",
    }


def test_hybrid_file_ranking_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    case = load_retrieval_corpus(CORPUS).cases[-1]
    repository = materialize_case(case, tmp_path)
    monkeypatch.setenv("REPOPILOT_RETRIEVAL_CACHE_NAMESPACE", "unit-hybrid-determinism")
    config = RetrievalConfig(strategy="hybrid")
    first = HybridSelector().select(repository, repository, case.issue, config)
    second = HybridSelector().select(repository, repository, case.issue, config)
    assert [candidate.path for candidate in first.candidates] == [candidate.path for candidate in second.candidates]
    assert [candidate.fused_score for candidate in first.candidates] == [candidate.fused_score for candidate in second.candidates]
