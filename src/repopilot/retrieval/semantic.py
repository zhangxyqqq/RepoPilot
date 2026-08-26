from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from repopilot.retrieval.chunking import CHUNKER_VERSION, chunk_repository
from repopilot.retrieval.common import tokenize
from repopilot.retrieval.contracts import ContextCandidate, RetrievalConfig, RetrievalResult


_SOURCE_MODEL = Path(__file__).resolve().parents[3] / "configs" / "retrieval" / "semantic_model.v1.json"
_PACKAGED_MODEL = Path(__file__).resolve().parents[1] / "configs" / "retrieval" / "semantic_model.v1.json"
DEFAULT_MODEL_PATH = _SOURCE_MODEL if _SOURCE_MODEL.exists() else _PACKAGED_MODEL
CACHE_ROOT = Path("/tmp/repopilot-retrieval-cache-v1")
PINNED_MODEL_SHA256 = "67df4c4ab32c1cd951857e99d1452de89f2d8bab9ecbb3f75186254aed36c101"


class SemanticModelError(RuntimeError):
    pass


class LocalSemanticEncoder:
    def __init__(self, path: Path = DEFAULT_MODEL_PATH, *, expected_sha256: str | None = None):
        try:
            content = path.read_bytes()
            raw = json.loads(content)
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticModelError(f"cannot load local semantic model: {exc}") from exc
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise SemanticModelError("local semantic model weight hash mismatch")
        if set(raw) != {"schema_version", "model_id", "model_version", "hash_dimensions", "concept_weight", "token_weight", "concepts"}:
            raise SemanticModelError("semantic model has missing or unknown fields")
        if raw["schema_version"] != 1 or raw["model_version"] != 1 or raw["model_id"] != "repopilot-semantic-mini":
            raise SemanticModelError("unsupported local semantic model")
        self.model_id = raw["model_id"]
        self.version = raw["model_version"]
        self.content_hash = digest
        self.hash_dimensions = int(raw["hash_dimensions"])
        self.concept_weight = float(raw["concept_weight"])
        self.token_weight = float(raw["token_weight"])
        self.concepts = tuple(frozenset(map(str, concept)) for concept in raw["concepts"])

    @property
    def dimensions(self) -> int:
        return len(self.concepts) + self.hash_dimensions

    def encode(self, text: str) -> tuple[float, ...]:
        tokens = tokenize(text)
        values = [0.0] * self.dimensions
        token_set = set(tokens)
        for index, concept in enumerate(self.concepts):
            matches = len(token_set & concept)
            if matches:
                values[index] = self.concept_weight * math.log1p(matches)
        offset = len(self.concepts)
        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=8, person=b"RepoPilot").digest()
            bucket = int.from_bytes(digest, "big") % self.hash_dimensions
            sign = 1.0 if digest[0] & 1 else -1.0
            values[offset + bucket] += sign * self.token_weight
        norm = math.sqrt(sum(value * value for value in values))
        return tuple(value / norm for value in values) if norm else tuple(values)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class EmbeddingCache:
    def __init__(self, model: LocalSemanticEncoder, max_bytes: int, root: Path = CACHE_ROOT, *, namespace: str = "default"):
        namespace = os.environ.get("REPOPILOT_RETRIEVAL_CACHE_NAMESPACE", namespace)
        if not namespace.replace("-", "").replace("_", "").isalnum():
            namespace = "default"
        self.path = root / namespace
        self.path.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.max_bytes = max_bytes
        self.hits = 0
        self.misses = 0

    def _path(self, candidate: ContextCandidate) -> Path:
        key = hashlib.sha256(
            f"{self.model.content_hash}:chunker-{CHUNKER_VERSION}:{candidate.candidate_id}".encode()
        ).hexdigest()
        return self.path / f"{key}.json"

    def vector(self, candidate: ContextCandidate) -> tuple[float, ...]:
        path = self._path(candidate)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("model_hash") == self.model.content_hash and raw.get("candidate_id") == candidate.candidate_id:
                self.hits += 1
                return tuple(float(value) for value in raw["vector"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        self.misses += 1
        vector = self.model.encode(f"{candidate.path} {candidate.symbol or ''} {candidate.text}")
        payload = {
            "schema_version": 1,
            "model_hash": self.model.content_hash,
            "chunker_version": CHUNKER_VERSION,
            "candidate_id": candidate.candidate_id,
            "vector": vector,
        }
        encoded = json.dumps(payload, separators=(",", ":"))
        if len(encoded.encode()) <= self.max_bytes:
            path.write_text(encoded, encoding="utf-8")
            self._enforce_bound()
        return vector

    def _enforce_bound(self) -> None:
        files = sorted(self.path.glob("*.json"), key=lambda path: path.name)
        total = sum(path.stat().st_size for path in files)
        while total > self.max_bytes and files:
            victim = files.pop(0)
            size = victim.stat().st_size
            victim.unlink(missing_ok=True)
            total -= size

    def artifact(self) -> dict[str, Any]:
        size = sum(path.stat().st_size for path in self.path.glob("*.json"))
        return {
            "status": "warm" if self.hits else "cold",
            "hits": self.hits,
            "misses": self.misses,
            "bytes": size,
            "max_bytes": self.max_bytes,
            "location": "ephemeral_tmpfs" if str(self.path).startswith("/tmp/") else "ephemeral_local",
            "persistent_service": False,
        }


def _pack_chunks(candidates: list[ContextCandidate], config: RetrievalConfig) -> tuple[str, set[str], bool]:
    blocks: list[str] = []
    selected: set[str] = set()
    selected_files: set[str] = set()
    characters = 0
    truncated = False
    for candidate in candidates:
        if len(selected) >= config.max_chunks:
            truncated = True
            break
        if candidate.path not in selected_files and len(selected_files) >= config.max_files:
            continue
        header = f"{candidate.path}:{candidate.start_line}-{candidate.end_line} [{candidate.symbol or 'text'}]"
        block = f"{header}\n{candidate.text}".rstrip()
        added = len(block) + (2 if blocks else 0)
        if characters + added > config.max_chars:
            truncated = True
            continue
        blocks.append(block)
        selected.add(candidate.candidate_id)
        selected_files.add(candidate.path)
        characters += added
    if len(selected) < len(candidates):
        truncated = True
    return "\n\n".join(blocks), selected, truncated


class SemanticSelector:
    strategy = "semantic"
    version = 1

    def __init__(self, *, expected_model_sha256: str | None = PINNED_MODEL_SHA256):
        self.expected_model_sha256 = expected_model_sha256

    def select(self, root: Path, workspace: Path, issue: str, config: RetrievalConfig) -> RetrievalResult:
        started = perf_counter()
        model = LocalSemanticEncoder(expected_sha256=self.expected_model_sha256)
        initialized = perf_counter()
        chunks = list(chunk_repository(root, workspace, config))
        chunked = perf_counter()
        cache = EmbeddingCache(model, config.cache_max_bytes, namespace=config.cache_namespace)
        query = model.encode(issue)
        scores = {candidate.candidate_id: _cosine(query, cache.vector(candidate)) for candidate in chunks}
        ranked = sorted(chunks, key=lambda candidate: (-scores[candidate.candidate_id], candidate.path, candidate.start_line, candidate.candidate_id))
        context, selected_ids, truncated = _pack_chunks(ranked, config)
        finalized = tuple(
            replace(
                candidate,
                semantic_score=scores[candidate.candidate_id],
                semantic_rank=rank,
                selected=candidate.candidate_id in selected_ids,
                selection_reason="local_semantic_similarity",
                character_contribution=len(candidate.text) if candidate.candidate_id in selected_ids else 0,
            )
            for rank, candidate in enumerate(ranked, 1)
        )
        finished = perf_counter()
        return RetrievalResult(
            self.strategy, self.version, context, finalized, len({candidate.path for candidate in chunks}),
            len({candidate.path for candidate in finalized if candidate.selected}),
            sum(candidate.selected for candidate in finalized), len(context), truncated,
            {
                "model_initialization": (initialized - started) * 1000,
                "chunking": (chunked - initialized) * 1000,
                "embedding_and_ranking": (finished - chunked) * 1000,
                "total": (finished - started) * 1000,
            },
            cache.artifact(),
        )
