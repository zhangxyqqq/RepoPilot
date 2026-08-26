from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


TAXONOMY_SCHEMA_VERSION = 1
_SOURCE_TAXONOMY_PATH = Path(__file__).resolve().parents[3] / "configs" / "evaluation" / "failure_taxonomy.v1.json"
_PACKAGED_TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "configs" / "evaluation" / "failure_taxonomy.v1.json"
DEFAULT_TAXONOMY_PATH = _SOURCE_TAXONOMY_PATH if _SOURCE_TAXONOMY_PATH.exists() else _PACKAGED_TAXONOMY_PATH
PHASES = {"setup", "model", "tool", "policy", "retrieval", "edit", "test", "controller", "evaluation"}
RECOVERABILITY = {"recovered", "retryable_unrecovered", "non_retryable", "unknown"}
IMPACTS = {"informational", "efficiency_degradation", "task_failure", "harness_failure"}
CONFIDENCE = {"deterministic_rule", "benchmark_oracle_rule", "manual_hypothesis"}
SEVERITIES = {"warning", "error"}


class TaxonomyError(ValueError):
    """Raised when a taxonomy definition is invalid."""


@dataclass(frozen=True)
class FailureTaxonomy:
    taxonomy_id: str
    version: int
    definitions: dict[str, dict[str, Any]]
    source: str

    def artifact(self) -> dict[str, Any]:
        return {"taxonomy_id": self.taxonomy_id, "version": self.version, "schema_version": TAXONOMY_SCHEMA_VERSION, "source": self.source}


def load_failure_taxonomy(path: Path | None = None) -> FailureTaxonomy:
    taxonomy_path = (path or DEFAULT_TAXONOMY_PATH).resolve()
    try:
        raw = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaxonomyError(f"cannot load taxonomy {taxonomy_path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "taxonomy_id", "version", "labels"}:
        raise TaxonomyError("taxonomy has missing or unknown top-level fields")
    if raw["schema_version"] != TAXONOMY_SCHEMA_VERSION or raw["version"] != 1:
        raise TaxonomyError("unsupported taxonomy version")
    if raw["taxonomy_id"] != "repopilot-failures" or not isinstance(raw["labels"], list):
        raise TaxonomyError("unsupported taxonomy identity or labels")
    required = {"label", "phase", "observable_signal", "default_recoverability", "outcome_impact", "attribution_confidence", "severity"}
    definitions: dict[str, dict[str, Any]] = {}
    for item in raw["labels"]:
        if not isinstance(item, dict) or set(item) != required:
            raise TaxonomyError("taxonomy label has missing or unknown fields")
        label = item["label"]
        if not isinstance(label, str) or label in definitions:
            raise TaxonomyError(f"duplicate or invalid taxonomy label: {label!r}")
        if (
            item["phase"] not in PHASES
            or item["default_recoverability"] not in RECOVERABILITY
            or item["outcome_impact"] not in IMPACTS
            or item["attribution_confidence"] not in CONFIDENCE
            or item["severity"] not in SEVERITIES
            or not isinstance(item["observable_signal"], str)
            or not item["observable_signal"].strip()
            or not label.startswith(f"{item['phase']}.")
        ):
            raise TaxonomyError(f"invalid taxonomy dimensions for {label}")
        definitions[label] = dict(item)
    return FailureTaxonomy(raw["taxonomy_id"], raw["version"], definitions, str(taxonomy_path))


def _event_id(event: Mapping[str, Any]) -> str:
    return str(event.get("event_id") or f"unavailable:{event.get('sequence', '?')}")


def _message(event: Mapping[str, Any]) -> str:
    error = event.get("error") or {}
    payload = event.get("payload") or {}
    return str(error.get("message") or payload.get("error") or "").lower()


def _definition_result(
    taxonomy: FailureTaxonomy,
    label: str,
    evidence: Iterable[str],
    *,
    recovered: bool | None = None,
    recoverability_override: str | None = None,
    injected_event_ids: set[str] | None = None,
) -> dict[str, Any]:
    definition = taxonomy.definitions[label]
    recoverability = definition["default_recoverability"]
    if recoverability_override is not None:
        recoverability = recoverability_override
    elif recovered is True:
        recoverability = "recovered"
    evidence_ids = sorted(set(evidence))
    return {
        "label": label,
        "phase": definition["phase"],
        "observable_signal": definition["observable_signal"],
        "recoverability": recoverability,
        "outcome_impact": definition["outcome_impact"],
        "attribution_confidence": definition["attribution_confidence"],
        "evidence_event_ids": evidence_ids,
        "fault_origin": "injected" if any(event_id in (injected_event_ids or set()) for event_id in evidence_ids) else "natural",
    }


def classify_failures(
    events: Iterable[Mapping[str, Any]],
    *,
    evaluation: Mapping[str, Any] | None = None,
    taxonomy: FailureTaxonomy | None = None,
) -> dict[str, Any]:
    taxonomy = taxonomy or load_failure_taxonomy()
    ordered = sorted((dict(event) for event in events), key=lambda event: (int(event.get("sequence", 0)), _event_id(event)))
    evaluation = dict(evaluation or {})
    evidence: dict[str, set[str]] = {}
    recovered: dict[str, bool] = {}
    unclassified: set[str] = set()
    observed_facts: list[dict[str, Any]] = []
    later_success = any(event.get("type") == "run_finished" and (event.get("payload") or {}).get("success") for event in ordered)
    injected_event_ids = {
        _event_id(event)
        for event in ordered
        if bool((event.get("payload") or {}).get("injected")) or bool((event.get("error") or {}).get("injected"))
    }
    decision_by_evidence: dict[str, dict[str, Any]] = {}
    for event in ordered:
        if event.get("type") != "recovery_decision":
            continue
        payload = dict(event.get("payload") or {})
        for evidence_event_id in payload.get("evidence_event_ids") or []:
            decision_by_evidence[str(evidence_event_id)] = payload

    def add(label: str, event: Mapping[str, Any] | None = None, *, fact: str | None = None, was_recovered: bool | None = None) -> None:
        event_ids = evidence.setdefault(label, set())
        if event is not None:
            event_ids.add(_event_id(event))
        if was_recovered is not None:
            recovered[label] = recovered.get(label, False) or was_recovered
        if fact:
            observed_facts.append({"fact": fact, "evidence_event_ids": sorted(event_ids)})

    seen_calls: dict[str, tuple[int | None, str, bool]] = {}
    run_finished = next((event for event in reversed(ordered) if event.get("type") == "run_finished"), None)
    for event in ordered:
        event_type = str(event.get("type", ""))
        payload = event.get("payload") or {}
        error = event.get("error")
        message = _message(event)
        code = str((error or {}).get("code", ""))
        if event_type == "setup_error":
            stage = payload.get("setup_stage") or (error or {}).get("stage")
            if stage == "staging": add("setup.staging_failed", event)
            elif stage == "sandbox_start": add("setup.sandbox_start_failed", event)
        if event_type == "model_error":
            if code in {"malformed_output", "invalid_json"} or any(term in message for term in ("malformed", "invalid json", "could not parse")):
                add("model.malformed_output", event, was_recovered=later_success)
            else:
                add("model.provider_error", event, was_recovered=later_success)
        if event_type == "model_turn":
            action = payload.get("action") or {}
            if error and code in {"malformed_output", "empty_output"}:
                add("model.malformed_output", event, was_recovered=later_success)
            if action.get("kind") not in {"plan", "tool", "final"} or (action.get("kind") == "tool" and not action.get("tool_name")):
                add("model.invalid_action", event, was_recovered=later_success)
        if event_type == "tool_call":
            tool = str(payload.get("tool", ""))
            args_key = json.dumps(payload.get("arguments") or {}, sort_keys=True, separators=(",", ":"))
            revision = event.get("workspace_revision_before")
            call_key = f"{tool}:{args_key}"
            if call_key in seen_calls and seen_calls[call_key][0] == revision and seen_calls[call_key][2] and not error:
                add("tool.duplicate_call", event)
                evidence["tool.duplicate_call"].add(seen_calls[call_key][1])
            seen_calls[call_key] = (revision, _event_id(event), not bool(error))
            if error:
                if code == "unknown_tool": add("tool.unknown_tool", event, was_recovered=later_success)
                elif code in {"invalid_arguments", "validation_error"}: add("tool.invalid_arguments", event, was_recovered=later_success)
                elif code == "timeout" or event.get("status") == "timeout": add("tool.execution_timeout", event, was_recovered=later_success)
                else: add("tool.execution_error", event, was_recovered=later_success)
            if any(term in message for term in ("must remain inside", "path traversal", "absolute path", "outside repository")):
                add("policy.path_rejected", event)
            if any(term in message for term in ("protected test", "test files are protected", "cannot edit tests")) or payload.get("observation", {}).get("ignored_paths"):
                add("policy.test_edit_rejected", event)
            if tool == "run_tests" and any(term in message for term in ("command", "allowlist", "not allowed")):
                add("policy.command_rejected", event)
            if tool == "list_files" and (payload.get("observation") or {}).get("repository_context", {}).get("truncated"):
                add("retrieval.context_truncated", event)
            retrieval = (payload.get("observation") or {}).get("repository_context", {}).get("retrieval", {})
            fallback = retrieval.get("fallback") if isinstance(retrieval, dict) else None
            if isinstance(fallback, dict) and fallback.get("used"):
                add("retrieval.semantic_failed", event, was_recovered=True)
                add("retrieval.fallback_used", event, was_recovered=True)
            if tool == "apply_patch":
                observation = payload.get("observation") or {}
                if error and code != "response_lost": add("edit.patch_rejected", event, was_recovered=later_success)
                if payload.get("ok") and observation.get("changed") is False: add("edit.no_op", event, was_recovered=later_success)
            if tool == "run_tests":
                observation = payload.get("observation") or {}
                if observation.get("timed_out"): add("test.timed_out", event, was_recovered=later_success)
                if payload.get("ok") and observation.get("passed") is False and not observation.get("timed_out"):
                    add("test.public_failed", event, was_recovered=later_success)
        if error and event_type not in {"model_error", "tool_call", "setup_error"}:
            unclassified.add(_event_id(event))

    terminal_reason: dict[str, Any] = {"stop_reason": None, "classification": "unavailable", "evidence_event_ids": []}
    if run_finished:
        stop = (run_finished.get("payload") or {}).get("stop_reason")
        terminal_reason = {"stop_reason": stop, "classification": "unclassified", "evidence_event_ids": [_event_id(run_finished)]}
        stop_labels = {"iteration_limit": "controller.iteration_limit", "repair_limit": "controller.repair_limit", "total_timeout": "controller.total_timeout"}
        if stop in stop_labels:
            add(stop_labels[stop], run_finished)
            terminal_reason["classification"] = "failure_label"
        elif stop == "model_final" and not (run_finished.get("payload") or {}).get("success"):
            add("model.premature_final", run_finished)
            terminal_reason["classification"] = "failure_label"
        elif stop == "model_error" and any(label in evidence for label in ("model.provider_error", "model.malformed_output")):
            terminal_reason["classification"] = "failure_label"
        elif stop in {"tests_passed", "model_final", "transport_closed"} and (run_finished.get("payload") or {}).get("success"):
            terminal_reason["classification"] = "non_failure"
        else:
            unclassified.add(_event_id(run_finished))

    eval_event = str(evaluation.get("evidence_event_id") or "evaluation:oracle")
    public = evaluation.get("public_tests") or {}
    hidden = evaluation.get("hidden_tests") or {}
    if public.get("passed") is True and hidden.get("passed") is False and evaluation.get("harness_failed") is not True:
        evidence.setdefault("test.public_pass_hidden_fail", set()).add(eval_event)
    if evaluation.get("reference_integrity_passed") is False:
        evidence.setdefault("evaluation.reference_integrity_failed", set()).add(eval_event)
    if evaluation.get("harness_failed") is True:
        evidence.setdefault("evaluation.harness_failed", set()).add(eval_event)
    expected = set(evaluation.get("expected_fix_files") or [])
    selected = set(evaluation.get("retrieved_files") or [])
    if expected and "retrieved_files" in evaluation and not expected & selected:
        evidence.setdefault("retrieval.expected_file_missed", set()).add(eval_event)
    changed = set(evaluation.get("changed_files") or [])
    allowed_sets = [set(paths) for paths in evaluation.get("allowed_fix_sets") or []]
    if changed and allowed_sets and not any(changed <= allowed for allowed in allowed_sets):
        evidence.setdefault("edit.overbroad_change", set()).add(eval_event)

    def recovery_override(ids: set[str]) -> str | None:
        decisions = [decision_by_evidence[event_id] for event_id in ids if event_id in decision_by_evidence]
        if not decisions:
            return None
        if any(decision.get("action") in {"stop", "stop_total_timeout"} for decision in decisions):
            return "retryable_unrecovered" if any(bool(decision.get("retryable")) for decision in decisions) else "non_retryable"
        return None

    classifications = [
        _definition_result(
            taxonomy,
            label,
            ids,
            recovered=recovered.get(label),
            recoverability_override=recovery_override(ids) if not recovered.get(label) else None,
            injected_event_ids=injected_event_ids,
        )
        for label, ids in sorted(evidence.items())
    ]
    observed_facts.extend(
        {
            "fact": item["observable_signal"],
            "source": "observed_trace_or_evaluation_fact",
            "evidence_event_ids": item["evidence_event_ids"],
        }
        for item in classifications
    )
    availability = "complete" if ordered and evaluation else ("trace_only" if ordered else "unavailable")
    return {
        "taxonomy": taxonomy.artifact(),
        "classifications": classifications,
        "observed_facts": sorted(observed_facts, key=lambda fact: (fact["fact"], fact["evidence_event_ids"])),
        "manual_hypotheses": [],
        "unclassified_error_event_ids": sorted(unclassified),
        "unclassified_error_count": len(unclassified),
        "evidence_availability": availability,
        "historical_missing_evidence": availability != "complete",
        "terminal_reason": terminal_reason,
        "injected_faults": [
            {
                "event_id": _event_id(event),
                "fault_type": (event.get("payload") or {}).get("fault_type"),
                "boundary": (event.get("payload") or {}).get("boundary"),
            }
            for event in ordered
            if event.get("type") == "fault_injected"
        ],
        "recovery_decisions": [
            {"event_id": _event_id(event), **dict(event.get("payload") or {})}
            for event in ordered
            if event.get("type") == "recovery_decision"
        ],
    }


def aggregate_failure_analysis(case_results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    labels: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    recovery: Counter[str] = Counter()
    impacts: Counter[str] = Counter()
    unclassified = public_hidden = 0
    agent_cases = infrastructure_cases = recovered_cases = efficiency_cases = 0
    for case in case_results:
        analysis = case.get("failure_analysis") or {}
        classifications = analysis.get("classifications") or []
        unclassified += int(analysis.get("unclassified_error_count", 0))
        class_labels = {item["label"] for item in classifications}
        case_impacts = {item["outcome_impact"] for item in classifications}
        for item in classifications:
            labels[item["label"]] += 1
            stages[item["phase"]] += 1
            recovery[item["recoverability"]] += 1
            impacts[item["outcome_impact"]] += 1
        public_hidden += "test.public_pass_hidden_fail" in class_labels
        infrastructure_cases += "harness_failure" in case_impacts
        unrecovered_task_failure = any(
            item["outcome_impact"] == "task_failure" and item["recoverability"] != "recovered"
            for item in classifications
        )
        agent_cases += unrecovered_task_failure and "harness_failure" not in case_impacts
        recovered_cases += any(item["recoverability"] == "recovered" for item in classifications)
        efficiency_cases += bool(classifications) and case_impacts <= {"informational", "efficiency_degradation"} and bool(case.get("task_success"))
    return {
        "failure_incidence_by_label": dict(sorted(labels.items())),
        "failure_stage_distribution": dict(sorted(stages.items())),
        "recoverability_distribution": dict(sorted(recovery.items())),
        "outcome_impact_distribution": dict(sorted(impacts.items())),
        "unclassified_error_count": unclassified,
        "public_pass_hidden_fail_count": public_hidden,
        "agent_behavioral_failure_cases": agent_cases,
        "infrastructure_harness_failure_cases": infrastructure_cases,
        "recovered_failure_cases": recovered_cases,
        "efficiency_only_degradation_cases": efficiency_cases,
    }
