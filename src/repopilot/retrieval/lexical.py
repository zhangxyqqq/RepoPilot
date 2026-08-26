from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter

from repopilot.retrieval.common import bm25_scores, file_candidate, render_ranked_context, safe_text_files
from repopilot.retrieval.contracts import RetrievalConfig, RetrievalResult


class LexicalSelector:
    strategy = "lexical"
    version = 1

    def select(self, root: Path, workspace: Path, issue: str, config: RetrievalConfig) -> RetrievalResult:
        started = perf_counter()
        files = safe_text_files(root, workspace, config)
        candidates = [file_candidate(path, workspace) for path in files]
        collected = perf_counter()
        scores = bm25_scores(issue, candidates)
        ranked = sorted(candidates, key=lambda item: (-scores[item.candidate_id], item.path))
        context, selected_ids, truncated = render_ranked_context(ranked, config)
        finalized = [
            replace(
                candidate,
                lexical_score=scores[candidate.candidate_id],
                lexical_rank=rank,
                selected=candidate.candidate_id in selected_ids,
                selection_reason="bm25_issue_match" if scores[candidate.candidate_id] > 0 else "stable_path_tiebreak",
                character_contribution=min(len(candidate.text), 1_200) if candidate.candidate_id in selected_ids else 0,
            )
            for rank, candidate in enumerate(ranked, 1)
        ]
        finished = perf_counter()
        return RetrievalResult(
            self.strategy, self.version, context, tuple(finalized), len(files),
            len({candidate.path for candidate in finalized if candidate.selected}),
            sum(candidate.selected for candidate in finalized), len(context), truncated,
            {
                "collection": (collected - started) * 1000,
                "ranking_and_packing": (finished - collected) * 1000,
                "total": (finished - started) * 1000,
            },
            {"status": "not_applicable", "persistent": False},
        )
