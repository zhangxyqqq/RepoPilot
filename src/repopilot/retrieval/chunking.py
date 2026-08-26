from __future__ import annotations

import ast
import hashlib
from dataclasses import replace
from pathlib import Path

from repopilot.retrieval.common import file_candidate, safe_text_files
from repopilot.retrieval.contracts import ContextCandidate, RetrievalConfig


CHUNKER_VERSION = 1


def _chunk_id(path: str, start: int, end: int, symbol: str | None, text: str) -> str:
    payload = f"v{CHUNKER_VERSION}:{path}:{start}:{end}:{symbol or ''}:{hashlib.sha256(text.encode()).hexdigest()}"
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _candidate(base: ContextCandidate, start: int, end: int, symbol: str | None, lines: list[str]) -> ContextCandidate:
    text = "\n".join(lines[start - 1:end])
    return replace(
        base,
        candidate_id=_chunk_id(base.path, start, end, symbol, text),
        start_line=start,
        end_line=end,
        symbol=symbol,
        text=text,
    )


def _python_chunks(base: ContextCandidate) -> list[ContextCandidate]:
    lines = base.text.splitlines()
    if not lines:
        return [_candidate(base, 1, 1, None, [])]
    try:
        tree = ast.parse(base.text, filename=base.path)
    except SyntaxError:
        return [_candidate(base, 1, len(lines), None, lines)]
    chunks: list[ContextCandidate] = []
    covered: set[int] = set()
    for node in tree.body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = max(1, int(node.lineno))
        end = min(len(lines), int(getattr(node, "end_lineno", node.lineno)))
        chunks.append(_candidate(base, start, end, node.name, lines))
        covered.update(range(start, end + 1))
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    member_start = max(1, int(member.lineno))
                    member_end = min(len(lines), int(getattr(member, "end_lineno", member.lineno)))
                    chunks.append(_candidate(base, member_start, member_end, f"{node.name}.{member.name}", lines))
    module_lines = [line for number, line in enumerate(lines, 1) if number not in covered]
    if module_lines:
        module_text = "\n".join(module_lines)
        chunks.insert(0, replace(
            base,
            candidate_id=_chunk_id(base.path, 1, len(lines), "<module>", module_text),
            start_line=1,
            end_line=len(lines),
            symbol="<module>",
            text=module_text,
        ))
    return chunks or [_candidate(base, 1, len(lines), None, lines)]


def _bounded_text_chunks(base: ContextCandidate, *, lines_per_chunk: int = 80) -> list[ContextCandidate]:
    lines = base.text.splitlines()
    return [
        _candidate(base, start, min(len(lines), start + lines_per_chunk - 1), None, lines)
        for start in range(1, max(2, len(lines) + 1), lines_per_chunk)
    ]


def chunk_repository(root: Path, workspace: Path, config: RetrievalConfig) -> tuple[ContextCandidate, ...]:
    chunks: list[ContextCandidate] = []
    for path in safe_text_files(root, workspace, config):
        base = file_candidate(path, workspace)
        candidates = _python_chunks(base) if path.suffix == ".py" else _bounded_text_chunks(base)
        for candidate in candidates:
            if len(chunks) >= config.max_chunks:
                return tuple(chunks)
            chunks.append(candidate)
    return tuple(chunks)
