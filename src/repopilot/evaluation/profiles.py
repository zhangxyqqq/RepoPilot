from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from repopilot.config import RunLimits


PROFILE_SCHEMA_VERSION = 1
_SOURCE_PROFILE_PATH = Path(__file__).resolve().parents[3] / "configs" / "evaluation" / "default.json"
_PACKAGED_PROFILE_PATH = Path(__file__).resolve().parents[1] / "configs" / "evaluation" / "default.json"
DEFAULT_PROFILE_PATH = _SOURCE_PROFILE_PATH if _SOURCE_PROFILE_PATH.exists() else _PACKAGED_PROFILE_PATH
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_TOP_LEVEL = {
    "schema_version", "profile_id", "profile_version", "track", "model_repetitions",
    "limits", "taxonomy", "required_metrics", "acceptance_thresholds", "recovery_policy",
}
_LIMIT_FIELDS = set(RunLimits.__dataclass_fields__)
_TAXONOMY_FIELDS = {"taxonomy_id", "version"}
_THRESHOLD_FIELDS = {
    "tasks_succeeded_min", "public_test_cases_passed_min", "hidden_test_cases_passed_min",
    "localization_f1_mean_min", "unclassified_error_count_max",
    "unsafe_retry_count_max", "duplicate_mutation_count_max",
    "revision_divergence_count_max", "budget_overshoot_count_max",
}
_RECOVERY_FIELDS = {"version", "max_model_retries", "max_read_only_retries", "max_test_timeout_retries"}
_METRICS = {
    "task_success", "public_tests", "hidden_tests", "localization_f1", "tool_calls",
    "latency_ms", "token_usage", "failure_analysis",
}


class EvaluationProfileError(ValueError):
    """Raised when an evaluation profile is unsafe or invalid."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationProfileError(f"duplicate profile field: {key}")
        result[key] = value
    return result


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationProfileError(f"{name} must be an object")
    return value


def _only_fields(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EvaluationProfileError(f"unknown {name} field: {unknown[0]}")


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvaluationProfileError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class EvaluationProfile:
    profile_id: str
    profile_version: int
    resolved: dict[str, Any]
    content_hash: str
    source: str

    @property
    def run_limits(self) -> RunLimits:
        return RunLimits(**self.resolved["limits"])

    @property
    def taxonomy_reference(self) -> dict[str, Any]:
        return dict(self.resolved["taxonomy"])

    def artifact(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "schema_version": PROFILE_SCHEMA_VERSION,
            "content_hash": self.content_hash,
            "resolved_configuration": self.resolved,
            "source": self.source,
        }


def resolve_profile(raw: Mapping[str, Any], *, source: str = "<memory>") -> EvaluationProfile:
    value = dict(raw)
    _only_fields(value, _TOP_LEVEL, "profile")
    if value.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise EvaluationProfileError(f"unsupported profile schema version: {value.get('schema_version')!r}")
    profile_id = value.get("profile_id")
    if not isinstance(profile_id, str) or not _IDENTIFIER.fullmatch(profile_id):
        raise EvaluationProfileError("profile_id must be a lowercase data identifier")
    profile_version = _integer(value.get("profile_version"), "profile_version", minimum=1)
    track = value.get("track", "controlled")
    if track not in {"controlled", "reliability"}:
        raise EvaluationProfileError("profiles support only controlled or reliability tracks")
    repetitions = _integer(value.get("model_repetitions", 1), "model_repetitions", minimum=1)
    if repetitions != 1:
        raise EvaluationProfileError("Phase 2 supports exactly one model repetition")

    limits = asdict(RunLimits())
    supplied_limits = _object(value.get("limits", {}), "limits")
    _only_fields(supplied_limits, _LIMIT_FIELDS, "limits")
    for key, item in supplied_limits.items():
        limits[key] = _integer(item, f"limits.{key}", minimum=1)

    taxonomy = _object(value.get("taxonomy", {}), "taxonomy")
    _only_fields(taxonomy, _TAXONOMY_FIELDS, "taxonomy")
    taxonomy_id = taxonomy.get("taxonomy_id", "repopilot-failures")
    if not isinstance(taxonomy_id, str) or not _IDENTIFIER.fullmatch(taxonomy_id):
        raise EvaluationProfileError("taxonomy.taxonomy_id must be a lowercase data identifier")
    taxonomy_version = _integer(taxonomy.get("version", 1), "taxonomy.version", minimum=1)

    recovery = _object(value.get("recovery_policy", {}), "recovery_policy")
    _only_fields(recovery, _RECOVERY_FIELDS, "recovery_policy")
    resolved_recovery = {
        "version": _integer(recovery.get("version", 1), "recovery_policy.version", minimum=1),
        "max_model_retries": _integer(recovery.get("max_model_retries", 2), "recovery_policy.max_model_retries"),
        "max_read_only_retries": _integer(recovery.get("max_read_only_retries", 1), "recovery_policy.max_read_only_retries"),
        "max_test_timeout_retries": _integer(recovery.get("max_test_timeout_retries", 0), "recovery_policy.max_test_timeout_retries"),
    }
    if resolved_recovery["version"] != 1 or any(resolved_recovery[key] > 10 for key in resolved_recovery if key != "version"):
        raise EvaluationProfileError("recovery policy version/budget is unsupported")

    metrics = value.get("required_metrics", sorted(_METRICS))
    if not isinstance(metrics, list) or not metrics or any(not isinstance(item, str) for item in metrics):
        raise EvaluationProfileError("required_metrics must be a non-empty string array")
    if len(metrics) != len(set(metrics)):
        raise EvaluationProfileError("required_metrics contains duplicates")
    unknown_metrics = sorted(set(metrics) - _METRICS)
    if unknown_metrics:
        raise EvaluationProfileError(f"unsupported required metric: {unknown_metrics[0]}")

    thresholds = _object(value.get("acceptance_thresholds", {}), "acceptance_thresholds")
    _only_fields(thresholds, _THRESHOLD_FIELDS, "acceptance_thresholds")
    resolved_thresholds: dict[str, int | float] = {}
    for key, item in thresholds.items():
        if key == "localization_f1_mean_min":
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 <= item <= 1:
                raise EvaluationProfileError(f"acceptance_thresholds.{key} must be between 0 and 1")
            resolved_thresholds[key] = float(item)
        else:
            resolved_thresholds[key] = _integer(item, f"acceptance_thresholds.{key}")

    resolved = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "track": track,
        "model_repetitions": repetitions,
        "limits": limits,
        "taxonomy": {"taxonomy_id": taxonomy_id, "version": taxonomy_version},
        "recovery_policy": resolved_recovery,
        "required_metrics": metrics,
        "acceptance_thresholds": resolved_thresholds,
    }
    return EvaluationProfile(
        profile_id=profile_id,
        profile_version=profile_version,
        resolved=resolved,
        content_hash="sha256:" + hashlib.sha256(_canonical(resolved)).hexdigest(),
        source=source,
    )


def load_evaluation_profile(path: Path | None = None) -> EvaluationProfile:
    profile_path = (path or DEFAULT_PROFILE_PATH).resolve()
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationProfileError(f"cannot load evaluation profile {profile_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvaluationProfileError("evaluation profile root must be an object")
    return resolve_profile(raw, source=str(profile_path))


def evaluate_profile_acceptance(profile: EvaluationProfile, aggregate: Mapping[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for threshold, expected in profile.resolved["acceptance_thresholds"].items():
        if threshold.endswith("_min"):
            metric = threshold[:-4]
            passed = metric in aggregate and aggregate[metric] >= expected
            operator = ">="
        else:
            metric = threshold[:-4]
            passed = metric in aggregate and aggregate[metric] <= expected
            operator = "<="
        checks.append({"metric": metric, "operator": operator, "expected": expected, "actual": aggregate.get(metric), "passed": passed})
    return {"passed": all(check["passed"] for check in checks), "checks": checks}
