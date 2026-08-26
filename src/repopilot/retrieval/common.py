from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path

from repopilot.retrieval.contracts import ContextCandidate, RetrievalConfig


EXCLUDED_DIRECTORIES = frozenset({".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"})
TEXT_SUFFIXES = frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".md", ".toml", ".yaml", ".yml"})
TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}")


def tokenize(value: str) -> tuple[str, ...]:
    expanded: list[str] = []
    for raw in TOKEN_PATTERN.findall(value):
        lowered = raw.lower()
        expanded.append(lowered)
        expanded.extend(part for part in lowered.split("_") if len(part) >= 2)
        expanded.extend(
            part.lower()
            for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", raw)
            if len(part) >= 2
        )
    return tuple(expanded)


def safe_text_files(root: Path, workspace: Path, config: RetrievalConfig) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if name not in EXCLUDED_DIRECTORIES and not (Path(current) / name).is_symlink()
        )
        for name in sorted(names):
            path = Path(current) / name
            if (
                len(files) < config.max_scan_files
                and path.suffix.lower() in TEXT_SUFFIXES
                and path.is_file()
                and not path.is_symlink()
                and path.stat().st_size <= config.max_file_bytes
            ):
                resolved = path.resolve(strict=True)
                if resolved == workspace or workspace in resolved.parents:
                    files.append(path)
    return files


def file_candidate(path: Path, workspace: Path) -> ContextCandidate:
    relative = str(path.relative_to(workspace))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    lines = text.splitlines()
    role = "test" if "tests" in path.parts or path.name.startswith("test_") else "source"
    candidate_id = hashlib.sha256(f"file:{relative}".encode()).hexdigest()[:16]
    return ContextCandidate(candidate_id, relative, role, 1, max(1, len(lines)), text=text)


def bm25_scores(query: str, candidates: list[ContextCandidate]) -> dict[str, float]:
    query_terms = sorted(set(tokenize(query)))
    documents = [tokenize(f"{candidate.path} {candidate.symbol or ''} {candidate.text}") for candidate in candidates]
    average_length = sum(map(len, documents)) / max(1, len(documents))
    document_frequency = {
        term: sum(term in set(document) for document in documents)
        for term in query_terms
    }
    scores: dict[str, float] = {}
    k1, b = 1.2, 0.75
    for candidate, document in zip(candidates, documents, strict=True):
        frequencies = {term: document.count(term) for term in query_terms}
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            inverse = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(document) / max(1.0, average_length))
            score += inverse * frequency * (k1 + 1) / denominator
        scores[candidate.candidate_id] = score
    return scores


def render_ranked_context(candidates: list[ContextCandidate], config: RetrievalConfig) -> tuple[str, set[str], bool]:
    lines: list[str] = []
    selected: set[str] = set()
    characters = 0
    truncated = False
    for candidate in candidates:
        if len(selected) >= config.max_files:
            truncated = True
            break
        excerpt = candidate.text[: max(0, min(1_200, config.max_chars - characters))].rstrip()
        block = f"{candidate.path} [role={candidate.role}]\n{excerpt}" if excerpt else f"{candidate.path} [role={candidate.role}]"
        added = len(block) + (2 if lines else 0)
        if characters + added > config.max_chars:
            truncated = True
            break
        lines.append(block)
        selected.add(candidate.candidate_id)
        characters += added
    if len(selected) < len(candidates):
        truncated = True
    return "\n\n".join(lines), selected, truncated
