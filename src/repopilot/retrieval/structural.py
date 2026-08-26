from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from time import perf_counter

try:
    from repopilot.sandbox.repository_context import (
        _issue_terms,
        _parse_outline,
        _python_files,
        _relevance,
        build_repository_context,
    )
except ModuleNotFoundError:  # container-local import path
    from repository_context import (  # type: ignore[no-redef]
        _issue_terms,
        _parse_outline,
        _python_files,
        _relevance,
        build_repository_context,
    )

from repopilot.retrieval.common import file_candidate
from repopilot.retrieval.contracts import ContextCandidate, RetrievalConfig, RetrievalResult


_MAP_PATH = re.compile(r"^(.+?) \[module=.+, role=(source|test)\]$")


class StructuralSelector:
    strategy = "structural"
    version = 1

    def select(self, root: Path, workspace: Path, issue: str, config: RetrievalConfig) -> RetrievalResult:
        started = perf_counter()
        context = build_repository_context(
            root,
            workspace,
            issue=issue,
            max_files=config.max_files,
            max_symbols=config.max_symbols,
            max_chars=config.max_chars,
            max_file_bytes=config.max_file_bytes,
            max_scan_files=config.max_scan_files,
        )
        parsed = perf_counter()
        outlines = []
        for order, path in enumerate(_python_files(root)[: config.max_scan_files]):
            if path.stat().st_size > config.max_file_bytes:
                continue
            try:
                outlines.append(_parse_outline(path, path.relative_to(workspace), order))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
        terms = _issue_terms(issue)
        scores = {
            outline.path: _relevance(terms, [str(outline.path), outline.module, *(symbol.name for symbol in outline.symbols)])
            + (1 if outline.role == "source" else 0)
            for outline in outlines
        }
        directly_relevant = {
            outline.module for outline in outlines if scores[outline.path] > (1 if outline.role == "source" else 0)
        }
        neighbor_paths: set[Path] = set()
        for outline in outlines:
            if any(
                imported.lstrip(".") == module or imported.lstrip(".").startswith(module + ".")
                for imported in outline.imports for module in directly_relevant
            ):
                scores[outline.path] += 3
                neighbor_paths.add(outline.path)
        ranked = sorted(outlines, key=lambda outline: (-scores[outline.path], str(outline.path)))
        selected_paths = {
            match.group(1)
            for line in context["map"].splitlines()
            if (match := _MAP_PATH.match(line))
        }
        candidates: list[ContextCandidate] = []
        for rank, outline in enumerate(ranked, 1):
            raw = file_candidate(workspace / outline.path, workspace)
            score = float(scores[outline.path])
            reason = "issue_term_match" if score > (1 if outline.role == "source" else 0) else "stable_source_order"
            if outline.path in neighbor_paths:
                reason = "import_neighbor"
            candidates.append(replace(
                raw,
                structural_score=score,
                structural_rank=rank,
                selection_reason=reason,
                selected=str(outline.path) in selected_paths,
                character_contribution=len(raw.text) if str(outline.path) in selected_paths else 0,
            ))
        finished = perf_counter()
        return RetrievalResult(
            strategy=self.strategy,
            strategy_version=self.version,
            context=context["map"],
            candidates=tuple(candidates),
            scanned_files=len(_python_files(root)),
            selected_files=context["stats"]["mapped_files"],
            selected_chunks=context["stats"]["symbols"],
            context_characters=len(context["map"]),
            truncated=bool(context["truncated"]),
            latency_ms={
                "structural_map": (parsed - started) * 1000,
                "candidate_ranking": (finished - parsed) * 1000,
                "total": (finished - started) * 1000,
            },
            cache={"status": "not_applicable", "persistent": False},
            metadata={"legacy_context": context},
        )
