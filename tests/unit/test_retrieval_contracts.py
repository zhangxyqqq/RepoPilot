from __future__ import annotations

from pathlib import Path

import pytest

from repopilot.retrieval import LexicalSelector, RetrievalConfig, StructuralSelector
from repopilot.sandbox.repository_context import build_repository_context


def _repository(root: Path) -> None:
    for relative, content in {
        "src/payments/processor.py": "def capture_payment(amount):\n    return amount\n",
        "src/reports/payment_failures.py": "def payment_failure_report():\n    return []\n",
        "src/payments/refunds.py": "def refund_payment(amount):\n    return amount\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_structural_selector_preserves_existing_ast_map_exactly(tmp_path: Path) -> None:
    _repository(tmp_path)
    config = RetrievalConfig(max_files=2, max_symbols=10, max_chars=1000)
    expected = build_repository_context(
        tmp_path, tmp_path, issue="capture_payment fails", max_files=2, max_symbols=10, max_chars=1000
    )
    result = StructuralSelector().select(tmp_path, tmp_path, "capture_payment fails", config)

    assert result.context == expected["map"]
    assert result.truncated is expected["truncated"]
    assert result.strategy == "structural"
    assert [candidate.structural_rank for candidate in result.candidates] == list(range(1, 4))


def test_lexical_selector_is_deterministic_and_bounded(tmp_path: Path) -> None:
    _repository(tmp_path)
    config = RetrievalConfig(strategy="lexical", max_files=2, max_chars=300)
    first = LexicalSelector().select(tmp_path, tmp_path, "capture payment amount", config)
    second = LexicalSelector().select(tmp_path, tmp_path, "capture payment amount", config)

    assert [candidate.path for candidate in first.candidates] == [candidate.path for candidate in second.candidates]
    assert [candidate.lexical_score for candidate in first.candidates] == [candidate.lexical_score for candidate in second.candidates]
    assert first.candidates[0].path == "src/payments/processor.py"
    assert first.context_characters <= 300
    assert first.selected_files <= 2


def test_retrieval_config_rejects_unknown_or_unbounded_values() -> None:
    with pytest.raises(ValueError, match="unsupported retrieval strategy"):
        RetrievalConfig(strategy="remote")
    with pytest.raises(ValueError, match="max_files"):
        RetrievalConfig(max_files=0)
