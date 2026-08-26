from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from repopilot.retrieval import RetrievalConfig, select_context
from repopilot.trajectory import redact_value


RETRIEVAL_BENCHMARK_SCHEMA_VERSION = 1
_PROFILE_FIELDS = {"schema_version", "profile_id", "profile_version", "strategies", "budgets", "fusion", "semantic_model", "cache", "acceptance"}


class RetrievalBenchmarkError(ValueError):
    pass


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    split: str
    category: str
    issue: str
    expected_fix_files: tuple[str, ...]
    expected_symbols: tuple[str, ...]
    semantic_help_expected: bool
    files: dict[str, str]


@dataclass(frozen=True)
class RetrievalCorpus:
    benchmark_id: str
    version: int
    cases: tuple[RetrievalCase, ...]
    content_hash: str
    source: str

    def artifact(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "version": self.version,
            "schema_version": RETRIEVAL_BENCHMARK_SCHEMA_VERSION,
            "content_hash": self.content_hash,
            "source": self.source,
            "split_counts": {
                split: sum(case.split == split for case in self.cases)
                for split in ("dev", "held_out")
            },
        }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RetrievalBenchmarkError(f"duplicate field: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise RetrievalBenchmarkError(f"cannot load {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RetrievalBenchmarkError(f"{path} must contain an object")
    return raw


def load_retrieval_profile(path: Path) -> tuple[RetrievalConfig, dict[str, Any]]:
    raw = _load_json(path.resolve())
    if set(raw) != _PROFILE_FIELDS or raw["schema_version"] != 1 or raw["profile_version"] != 1:
        raise RetrievalBenchmarkError("retrieval profile has missing, unknown, or unsupported fields")
    if raw["profile_id"] != "retrieval-comparison-v1":
        raise RetrievalBenchmarkError("unsupported retrieval profile identity")
    if raw["strategies"] != ["structural", "lexical", "semantic", "hybrid"]:
        raise RetrievalBenchmarkError("retrieval strategies must be frozen in canonical order")
    budgets = raw["budgets"]
    fusion = raw["fusion"]
    cache = raw["cache"]
    if set(budgets) != {"max_files", "max_chunks", "max_symbols", "max_chars", "max_file_bytes", "max_scan_files"}:
        raise RetrievalBenchmarkError("invalid retrieval budget fields")
    if set(fusion) != {"rrf_k", "structural_weight", "lexical_weight", "semantic_weight"} or set(cache) != {"max_bytes"}:
        raise RetrievalBenchmarkError("invalid fusion or cache fields")
    config = RetrievalConfig(**budgets, **fusion, cache_max_bytes=cache["max_bytes"])
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    artifact = {
        "profile_id": raw["profile_id"],
        "profile_version": 1,
        "schema_version": 1,
        "content_hash": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "resolved_configuration": raw,
        "source": str(path.resolve()),
    }
    return config, artifact


def load_retrieval_corpus(path: Path) -> RetrievalCorpus:
    source = path.resolve()
    raw = _load_json(source)
    if set(raw) != {"schema_version", "benchmark_id", "version", "cases"}:
        raise RetrievalBenchmarkError("retrieval corpus has missing or unknown top-level fields")
    if raw["schema_version"] != 1 or raw["version"] != 1 or raw["benchmark_id"] != "repopilot-retrieval-challenge":
        raise RetrievalBenchmarkError("unsupported retrieval corpus")
    if not isinstance(raw["cases"], list) or not 8 <= len(raw["cases"]) <= 12:
        raise RetrievalBenchmarkError("retrieval corpus must contain 8 to 12 cases")
    cases: list[RetrievalCase] = []
    seen: set[str] = set()
    required = {"case_id", "split", "category", "issue", "expected_fix_files", "expected_symbols", "semantic_help_expected", "files"}
    for item in raw["cases"]:
        if not isinstance(item, dict) or set(item) != required:
            raise RetrievalBenchmarkError("retrieval case has missing or unknown fields")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or case_id in seen:
            raise RetrievalBenchmarkError(f"duplicate or invalid case_id: {case_id!r}")
        seen.add(case_id)
        if item["split"] not in {"dev", "held_out"}:
            raise RetrievalBenchmarkError(f"invalid split for {case_id}")
        files = item["files"]
        expected = item["expected_fix_files"]
        if not isinstance(files, dict) or len(files) < 6 or not isinstance(expected, list) or not expected:
            raise RetrievalBenchmarkError(f"invalid files or oracle for {case_id}")
        for path, content in files.items():
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or not isinstance(content, str):
                raise RetrievalBenchmarkError(f"unsafe fixture path for {case_id}: {path}")
        if not set(expected) <= set(files):
            raise RetrievalBenchmarkError(f"oracle file missing from repository fixture: {case_id}")
        cases.append(RetrievalCase(
            case_id, item["split"], str(item["category"]), str(item["issue"]),
            tuple(map(str, expected)), tuple(map(str, item["expected_symbols"])),
            bool(item["semantic_help_expected"]), dict(files),
        ))
    if not any(case.split == "dev" for case in cases) or not any(case.split == "held_out" for case in cases):
        raise RetrievalBenchmarkError("both development and held-out splits are required")
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return RetrievalCorpus(raw["benchmark_id"], 1, tuple(cases), "sha256:" + hashlib.sha256(canonical).hexdigest(), str(source))


def materialize_case(case: RetrievalCase, root: Path) -> Path:
    repository = root / case.case_id / "repository"
    for relative, content in case.files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    forbidden = {"expected_fix_files", "expected_symbols", *case.expected_fix_files}
    staged_text = "\n".join(path.read_text(encoding="utf-8") for path in repository.rglob("*") if path.is_file())
    if any(value in staged_text for value in forbidden):
        raise RetrievalBenchmarkError(f"oracle leakage detected in staged repository for {case.case_id}")
    return repository


def _rank_for_expected(candidates: Iterable[dict[str, Any]], expected: set[str]) -> int | None:
    ranked_files: list[str] = []
    for item in candidates:
        if item["path"] not in ranked_files:
            ranked_files.append(item["path"])
    ranks = [index for index, path in enumerate(ranked_files, 1) if path in expected]
    return min(ranks) if ranks else None


def _aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(cases)
    return {
        "cases": count,
        "recall_at_1": sum(case["expected_file_rank"] is not None and case["expected_file_rank"] <= 1 for case in cases) / count,
        "recall_at_3": sum(case["expected_file_rank"] is not None and case["expected_file_rank"] <= 3 for case in cases) / count,
        "recall_at_5": sum(case["expected_file_rank"] is not None and case["expected_file_rank"] <= 5 for case in cases) / count,
        "mrr": sum(1 / case["expected_file_rank"] if case["expected_file_rank"] else 0 for case in cases) / count,
        "expected_symbol_hit_rate": sum(case["expected_symbol_hit"] for case in cases) / count,
        "context_precision_mean": statistics.fmean(case["context_precision"] for case in cases),
        "selected_files_mean": statistics.fmean(case["selected_files"] for case in cases),
        "selected_context_characters_mean": statistics.fmean(case["selected_context_characters"] for case in cases),
        "retrieval_latency_ms_mean": statistics.fmean(case["retrieval_latency_ms"] for case in cases),
        "truncation_rate": sum(case["truncated"] for case in cases) / count,
        "fallback_rate": sum(bool(case["fallback"]) for case in cases) / count,
    }


def _stress_repository(root: Path) -> Path:
    repository = root / "stress-workspace" / "repository"
    package = repository / "src" / "largeapp"
    package.mkdir(parents=True, exist_ok=True)
    for index in range(250):
        (package / f"module_{index:03}.py").write_text(
            f"def distractor_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    (package / "retry_policy.py").write_text(
        "def compute_retry_backoff(attempt):\n    return min(60, 2 ** attempt)\n",
        encoding="utf-8",
    )
    (package / "malformed.py").write_text("def broken(:\n", encoding="utf-8")
    return repository


def _stress_latency(output_directory: Path, base_config: RetrievalConfig, strategies: tuple[str, ...]) -> dict[str, Any]:
    repository = _stress_repository(output_directory)
    results: dict[str, Any] = {}
    for strategy in strategies:
        namespace = "stress-" + hashlib.sha256(f"{output_directory}:{strategy}".encode()).hexdigest()[:12]
        config = RetrievalConfig(**{
            **asdict(base_config),
            "strategy": strategy,
            "max_files": 8,
            "max_chunks": 320,
            "max_symbols": 8,
            "max_chars": 1_500,
            "cache_namespace": namespace,
        })
        cold = select_context(repository, repository, "Retry backoff is wrong after a failed attempt", config)
        warm = select_context(repository, repository, "Retry backoff is wrong after a failed attempt", config)
        results[strategy] = {
            "cold_latency_ms": cold.latency_ms["total"],
            "warm_latency_ms": warm.latency_ms["total"],
            "cold_cache": cold.cache,
            "warm_cache": warm.cache,
            "context_characters": warm.context_characters,
            "selected_files": warm.selected_files,
            "truncated": warm.truncated,
            "target_in_context": "src/largeapp/retry_policy.py" in warm.context,
            "cache_in_worktree": any("retrieval-cache" in str(path) for path in repository.rglob("*")),
        }
    return results


def evaluate_retrieval(
    corpus_path: Path,
    profile_path: Path,
    output_directory: Path,
    *,
    strategies: tuple[str, ...],
) -> dict[str, Any]:
    corpus = load_retrieval_corpus(corpus_path)
    base_config, profile = load_retrieval_profile(profile_path)
    allowed = set(profile["resolved_configuration"]["strategies"])
    if not strategies or set(strategies) - allowed:
        raise RetrievalBenchmarkError("requested an unknown retrieval strategy")
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for strategy in strategies:
        per_case: list[dict[str, Any]] = []
        cache_namespace = "eval-" + hashlib.sha256(str(output_directory).encode()).hexdigest()[:12]
        for case in corpus.cases:
            repository = materialize_case(case, output_directory / "workspaces")
            config = RetrievalConfig(**{**asdict(base_config), "strategy": strategy, "cache_namespace": cache_namespace})
            result = select_context(repository, repository, case.issue, config)
            warm_result = select_context(repository, repository, case.issue, config) if strategy in {"semantic", "hybrid"} else None
            candidates = [candidate.artifact() for candidate in result.candidates]
            ranked_files = list(dict.fromkeys(candidate["path"] for candidate in candidates))
            expected = set(case.expected_fix_files)
            rank = _rank_for_expected(candidates, expected)
            selected = [candidate for candidate in candidates if candidate["selected"]]
            selected_paths = {candidate["path"] for candidate in selected}
            selected_text = "\n".join(candidate.text for candidate in result.candidates if candidate.selected)
            per_case.append({
                "case_id": case.case_id,
                "split": case.split,
                "category": case.category,
                "semantic_help_expected": case.semantic_help_expected,
                "expected_fix_files": list(case.expected_fix_files),
                "expected_symbols": list(case.expected_symbols),
                "expected_file_rank": rank,
                "recall_at_1": bool(rank and rank <= 1),
                "recall_at_3": bool(rank and rank <= 3),
                "recall_at_5": bool(rank and rank <= 5),
                "reciprocal_rank": 1 / rank if rank else 0.0,
                "expected_symbol_hit": all(symbol in selected_text for symbol in case.expected_symbols),
                "context_precision": len(selected_paths & expected) / max(1, len(selected_paths)),
                "selected_files": result.selected_files,
                "selected_chunks": result.selected_chunks,
                "selected_context_characters": result.context_characters,
                "retrieval_latency_ms": result.latency_ms["total"],
                "cold_retrieval_latency_ms": result.latency_ms["total"],
                "warm_retrieval_latency_ms": warm_result.latency_ms["total"] if warm_result else None,
                "cache": result.cache,
                "warm_cache": warm_result.cache if warm_result else None,
                "truncated": result.truncated,
                "fallback": result.fallback,
                "ranked_candidates": candidates,
                "ranked_files": ranked_files,
            })
        results[strategy] = {
            "aggregate": _aggregate(per_case),
            "splits": {
                split: _aggregate([case for case in per_case if case["split"] == split])
                for split in ("dev", "held_out")
            },
            "cases": per_case,
        }
        warm_values = [case["warm_retrieval_latency_ms"] for case in per_case if case["warm_retrieval_latency_ms"] is not None]
        results[strategy]["aggregate"]["cold_retrieval_latency_ms_mean"] = statistics.fmean(
            case["cold_retrieval_latency_ms"] for case in per_case
        )
        results[strategy]["aggregate"]["warm_retrieval_latency_ms_mean"] = statistics.fmean(warm_values) if warm_values else None
    stress_latency = _stress_latency(output_directory, base_config, strategies)
    deterministic = {
        strategy: {
            "aggregate": {
                key: metric for key, metric in value["aggregate"].items() if "latency" not in key
            },
            "ranks": {case["case_id"]: case["expected_file_rank"] for case in value["cases"]},
        }
        for strategy, value in results.items()
    }
    fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(deterministic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    comparison: dict[str, Any] | None = None
    if {"structural", "semantic", "hybrid"} <= set(results):
        structural_cases = {case["case_id"]: case for case in results["structural"]["cases"]}
        semantic_cases = {case["case_id"]: case for case in results["semantic"]["cases"]}
        unique_at_3 = sorted(
            case_id for case_id, structural_case in structural_cases.items()
            if structural_case["semantic_help_expected"]
            and not structural_case["recall_at_3"]
            and semantic_cases[case_id]["recall_at_3"]
        )
        acceptance = profile["resolved_configuration"]["acceptance"]
        hybrid_r5 = results["hybrid"]["aggregate"]["recall_at_5"]
        best_single_r5 = max(
            results["structural"]["aggregate"]["recall_at_5"],
            results["semantic"]["aggregate"]["recall_at_5"],
        )
        checks = {
            "hybrid_recall_at_5": hybrid_r5 >= acceptance["hybrid_recall_at_5_min"],
            "hybrid_no_worse_than_best_single_recall_at_5": hybrid_r5 >= best_single_r5,
            "semantic_unique_paraphrase_recoveries_at_3": len(unique_at_3) >= acceptance["semantic_unique_paraphrase_recoveries_min"],
            "hybrid_cold_latency_target": stress_latency["hybrid"]["cold_latency_ms"] <= acceptance["cold_latency_ms_target"],
            "hybrid_warm_latency_target": stress_latency["hybrid"]["warm_latency_ms"] <= acceptance["warm_latency_ms_target"],
            "no_hybrid_fallbacks": results["hybrid"]["aggregate"]["fallback_rate"] == 0,
            "oracle_leakage_zero": True,
        }
        comparison = {
            "semantic_unique_paraphrase_recoveries_cutoff": 3,
            "semantic_unique_paraphrase_recoveries": unique_at_3,
            "hybrid_recall_at_5": hybrid_r5,
            "best_single_recall_at_5": best_single_r5,
            "checks": checks,
            "offline_gate_passed": all(checks.values()),
        }
    report = redact_value({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_track": "offline_retrieval",
        "corpus": corpus.artifact(),
        "profile": profile,
        "oracle_leakage_check": {"passed": True, "staged_oracle_fields": 0},
        "strategies": results,
        "stress_latency": stress_latency,
        "comparison": comparison,
        "deterministic_fingerprint": fingerprint,
    })
    json_path = output_directory / "retrieval.json"
    markdown_path = output_directory / "retrieval.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# RepoPilot offline retrieval evaluation", "",
        f"- Corpus: {corpus.benchmark_id} v{corpus.version}",
        f"- Corpus hash: {corpus.content_hash}",
        f"- Profile hash: {profile['content_hash']}",
        f"- Oracle leakage check: PASS", "",
        "| Strategy | R@1 | R@3 | R@5 | MRR | Precision | Latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, value in results.items():
        aggregate = value["aggregate"]
        lines.append(
            f"| {strategy} | {aggregate['recall_at_1']:.3f} | {aggregate['recall_at_3']:.3f} | "
            f"{aggregate['recall_at_5']:.3f} | {aggregate['mrr']:.3f} | "
            f"{aggregate['context_precision_mean']:.3f} | {aggregate['retrieval_latency_ms_mean']:.2f} |"
        )
    if comparison is not None:
        lines.extend([
            "",
            f"- Offline gate: {'PASS' if comparison['offline_gate_passed'] else 'FAIL'}",
            "- Semantic-only paraphrase recoveries at R@3: "
            + (", ".join(comparison["semantic_unique_paraphrase_recoveries"]) or "none"),
        ])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["report_paths"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report
