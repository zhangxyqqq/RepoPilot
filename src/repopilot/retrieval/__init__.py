from pathlib import Path

from repopilot.retrieval.contracts import ContextCandidate, ContextSelector, RetrievalConfig, RetrievalResult
from repopilot.retrieval.lexical import LexicalSelector
from repopilot.retrieval.hybrid import HybridSelector
from repopilot.retrieval.semantic import SemanticSelector
from repopilot.retrieval.structural import StructuralSelector


def select_context(root: Path, workspace: Path, issue: str, config: RetrievalConfig) -> RetrievalResult:
    selectors = {
        "structural": StructuralSelector,
        "lexical": LexicalSelector,
        "semantic": SemanticSelector,
        "hybrid": HybridSelector,
    }
    selector_type = selectors.get(config.strategy)
    if selector_type is None:
        raise ValueError(f"retrieval strategy is not installed: {config.strategy}")
    return selector_type().select(root, workspace, issue, config)


__all__ = [
    "ContextCandidate", "ContextSelector", "HybridSelector", "LexicalSelector", "RetrievalConfig",
    "RetrievalResult", "SemanticSelector", "StructuralSelector", "select_context",
]
