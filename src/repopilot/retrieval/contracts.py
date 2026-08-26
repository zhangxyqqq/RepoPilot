from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


RETRIEVAL_SCHEMA_VERSION = 1
RETRIEVAL_STRATEGIES = frozenset({"structural", "lexical", "semantic", "hybrid"})


@dataclass(frozen=True)
class RetrievalConfig:
    strategy: str = "structural"
    version: int = 1
    max_files: int = 100
    max_chunks: int = 300
    max_symbols: int = 300
    max_chars: int = 8_000
    max_file_bytes: int = 1_000_000
    max_scan_files: int = 5_000
    rrf_k: int = 60
    structural_weight: float = 1.0
    lexical_weight: float = 1.0
    semantic_weight: float = 1.0
    cache_max_bytes: int = 32_000_000
    cache_namespace: str = "default"

    def __post_init__(self) -> None:
        if self.strategy not in RETRIEVAL_STRATEGIES:
            raise ValueError(f"unsupported retrieval strategy: {self.strategy}")
        if self.version != 1:
            raise ValueError("unsupported retrieval configuration version")
        for name in (
            "max_files", "max_chunks", "max_symbols", "max_chars", "max_file_bytes",
            "max_scan_files", "rrf_k", "cache_max_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("structural_weight", "lexical_weight", "semantic_weight"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        if not self.cache_namespace.replace("-", "").replace("_", "").isalnum():
            raise ValueError("cache_namespace must be an alphanumeric data identifier")

    def artifact(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextCandidate:
    candidate_id: str
    path: str
    role: str
    start_line: int
    end_line: int
    symbol: str | None = None
    text: str = field(default="", repr=False, compare=False)
    structural_score: float | None = None
    structural_rank: int | None = None
    lexical_score: float | None = None
    lexical_rank: int | None = None
    semantic_score: float | None = None
    semantic_rank: int | None = None
    fused_score: float | None = None
    fused_rank: int | None = None
    selection_reason: str = ""
    selected: bool = False
    character_contribution: int = 0

    def artifact(self, *, include_text: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not include_text:
            result.pop("text", None)
        return result


@dataclass(frozen=True)
class RetrievalResult:
    strategy: str
    strategy_version: int
    context: str
    candidates: tuple[ContextCandidate, ...]
    scanned_files: int
    selected_files: int
    selected_chunks: int
    context_characters: int
    truncated: bool
    latency_ms: dict[str, float]
    cache: dict[str, Any]
    fallback: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def artifact(self, *, include_candidate_text: bool = False) -> dict[str, Any]:
        return {
            "schema_version": RETRIEVAL_SCHEMA_VERSION,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "context": self.context,
            "candidates": [candidate.artifact(include_text=include_candidate_text) for candidate in self.candidates],
            "stats": {
                "scanned_files": self.scanned_files,
                "selected_files": self.selected_files,
                "selected_chunks": self.selected_chunks,
                "context_characters": self.context_characters,
            },
            "truncated": self.truncated,
            "latency_ms": dict(sorted(self.latency_ms.items())),
            "cache": self.cache,
            "fallback": self.fallback,
            "metadata": self.metadata,
        }


class ContextSelector(Protocol):
    strategy: str
    version: int

    def select(self, root: Any, workspace: Any, issue: str, config: RetrievalConfig) -> RetrievalResult: ...
