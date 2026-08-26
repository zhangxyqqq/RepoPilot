from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter

from repopilot.retrieval.common import render_ranked_context
from repopilot.retrieval.contracts import RetrievalConfig, RetrievalResult
from repopilot.retrieval.lexical import LexicalSelector
from repopilot.retrieval.semantic import PINNED_MODEL_SHA256, SemanticModelError, SemanticSelector
from repopilot.retrieval.structural import StructuralSelector


class HybridSelector:
    strategy = "hybrid"
    version = 1

    def __init__(self, *, expected_model_sha256: str | None = PINNED_MODEL_SHA256):
        self.expected_model_sha256 = expected_model_sha256

    def select(self, root: Path, workspace: Path, issue: str, config: RetrievalConfig) -> RetrievalResult:
        started = perf_counter()
        structural = StructuralSelector().select(root, workspace, issue, config)
        lexical = LexicalSelector().select(root, workspace, issue, config)
        fallback = None
        try:
            semantic = SemanticSelector(expected_model_sha256=self.expected_model_sha256).select(root, workspace, issue, config)
        except (SemanticModelError, OSError, ValueError) as exc:
            semantic = None
            fallback = {
                "used": True,
                "from_strategy": "hybrid",
                "to_strategy": "structural_lexical",
                "error_code": "semantic_initialization_failed",
                "message": str(exc)[:500],
            }
        structural_by_path = {candidate.path: candidate for candidate in structural.candidates}
        lexical_by_path = {candidate.path: candidate for candidate in lexical.candidates}
        semantic_by_path = {}
        if semantic is not None:
            for candidate in semantic.candidates:
                current = semantic_by_path.get(candidate.path)
                if current is None or (candidate.semantic_rank or 10**9) < (current.semantic_rank or 10**9):
                    semantic_by_path[candidate.path] = candidate
        paths = sorted(set(structural_by_path) | set(lexical_by_path) | set(semantic_by_path))
        scored = []
        for path in paths:
            structural_candidate = structural_by_path.get(path)
            lexical_candidate = lexical_by_path.get(path)
            semantic_candidate = semantic_by_path.get(path)
            score = 0.0
            if structural_candidate and structural_candidate.structural_rank:
                score += config.structural_weight / (config.rrf_k + structural_candidate.structural_rank)
            if lexical_candidate and lexical_candidate.lexical_rank:
                score += config.lexical_weight / (config.rrf_k + lexical_candidate.lexical_rank)
            if semantic_candidate and semantic_candidate.semantic_rank:
                score += config.semantic_weight / (config.rrf_k + semantic_candidate.semantic_rank)
            base = lexical_candidate or structural_candidate or semantic_candidate
            assert base is not None
            scored.append((score, path, base, structural_candidate, lexical_candidate, semantic_candidate))
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
        raw_candidates = [item[2] for item in ranked]
        context, selected_ids, truncated = render_ranked_context(raw_candidates, config)
        candidates = tuple(
            replace(
                base,
                structural_score=structural_candidate.structural_score if structural_candidate else None,
                structural_rank=structural_candidate.structural_rank if structural_candidate else None,
                lexical_score=lexical_candidate.lexical_score if lexical_candidate else None,
                lexical_rank=lexical_candidate.lexical_rank if lexical_candidate else None,
                semantic_score=semantic_candidate.semantic_score if semantic_candidate else None,
                semantic_rank=semantic_candidate.semantic_rank if semantic_candidate else None,
                fused_score=score,
                fused_rank=rank,
                selected=base.candidate_id in selected_ids,
                selection_reason="weighted_reciprocal_rank_fusion" if fallback is None else "structural_lexical_fallback",
                character_contribution=min(len(base.text), 1_200) if base.candidate_id in selected_ids else 0,
            )
            for rank, (score, _, base, structural_candidate, lexical_candidate, semantic_candidate) in enumerate(ranked, 1)
        )
        finished = perf_counter()
        return RetrievalResult(
            self.strategy, self.version, context, candidates, len(paths),
            len({candidate.path for candidate in candidates if candidate.selected}),
            sum(candidate.selected for candidate in candidates), len(context), truncated,
            {
                "structural": structural.latency_ms["total"],
                "lexical": lexical.latency_ms["total"],
                "semantic": semantic.latency_ms["total"] if semantic else 0.0,
                "fusion": max(0.0, (finished - started) * 1000 - structural.latency_ms["total"] - lexical.latency_ms["total"] - (semantic.latency_ms["total"] if semantic else 0.0)),
                "total": (finished - started) * 1000,
            },
            semantic.cache if semantic else {"status": "unavailable", "persistent_service": False},
            fallback=fallback,
        )
