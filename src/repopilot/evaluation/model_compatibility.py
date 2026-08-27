from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from repopilot.agent.loop import AgentLoop
from repopilot.agent.recovery import RecoveryPolicy
from repopilot.config import RunLimits, SandboxConfig
from repopilot.evaluation.faults import (
    FaultInjectingBackend,
    FaultRuntime,
    FaultSpec,
    ReliabilityScenario,
)
from repopilot.evaluation.taxonomy import classify_failures
from repopilot.llm import ProviderConfig, create_model
from repopilot.llm.deepseek_adapter import DEEPSEEK_BASE_URL
from repopilot.llm.openai_adapter import OpenAICompatibleModel
from repopilot.llm.prompting import SYSTEM_PROMPT
from repopilot.models import ModelTurn, RunResult, ToolResult
from repopilot.sandbox import DockerSandbox, stage_repository
from repopilot.tools import ToolBackend
from repopilot.tools.definitions import PUBLIC_TOOL_DEFINITIONS, provider_tool_schemas
from repopilot.tools.registry import ToolRegistry
from repopilot.trajectory import TrajectoryRecorder, reconcile_summary, redact_value, summarize_trace
from repopilot.trajectory.reader import TraceReader


TRACK = "model_controller_compatibility"
DECISIONS = ("COMPATIBLE", "NOT COMPATIBLE", "INCONCLUSIVE / INFRASTRUCTURE FAILURE")
SWE_BENCH_CONTAMINATED_IDS = ("pallets__flask-5014", "pytest-dev__pytest-10051")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def corpus_hash(root: Path) -> str:
    items = [
        {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(
            item for item in root.rglob("*")
            if item.is_file()
            and not any(part in {"__pycache__", ".pytest_cache"} for part in item.relative_to(root).parts)
            and item.suffix not in {".pyc", ".pyo"}
        )
    ]
    return _sha(_canonical(items).encode())


def _tool_catalog_hash() -> str:
    value = {
        "catalog_version": 1,
        "definitions": [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
                "access": definition.access,
                "idempotency": definition.idempotency,
            }
            for definition in PUBLIC_TOOL_DEFINITIONS
        ],
        "provider_schemas": provider_tool_schemas(),
    }
    return _sha(_canonical(value).encode())


@dataclass(frozen=True)
class CompatibilityCase:
    case_id: str
    category: str
    repository: Path
    issue: str
    required_tools: tuple[str, ...]
    ordered_milestones: tuple[str, ...]
    expected_changed_files: tuple[str, ...]
    minimum_model_test_calls: int
    prelude: dict[str, Any] | None
    fault: dict[str, Any] | None
    completable: bool


def load_corpus(path: Path) -> tuple[dict[str, Any], list[CompatibilityCase]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "corpus_id", "corpus_version", "claim_boundary", "cases"}:
        raise ValueError("compatibility corpus has missing or unknown top-level fields")
    if raw["schema_version"] != 1 or raw["corpus_version"] != 1 or raw["corpus_id"] != "repopilot-model-controller-compatibility":
        raise ValueError("unsupported compatibility corpus identity or version")
    if any(value in _canonical(raw).casefold() for value in SWE_BENCH_CONTAMINATED_IDS):
        raise ValueError("compatibility corpus contains a contaminated SWE-bench instance")
    items = raw["cases"]
    expected_fields = {
        "case_id", "category", "repository", "issue", "required_tools", "ordered_milestones",
        "expected_changed_files", "minimum_model_test_calls", "prelude", "fault", "completable",
    }
    if not isinstance(items, list) or len(items) != 12:
        raise ValueError("compatibility corpus must freeze exactly 12 cases")
    cases: list[CompatibilityCase] = []
    seen: set[str] = set()
    public_tools = {definition.name for definition in PUBLIC_TOOL_DEFINITIONS}
    for item in items:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ValueError("compatibility case has missing or unknown fields")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or case_id in seen:
            raise ValueError("compatibility case IDs must be unique strings")
        seen.add(case_id)
        required = tuple(item["required_tools"])
        if not set(required) <= public_tools:
            raise ValueError(f"compatibility case references a non-public tool: {case_id}")
        repository = (path.parent / item["repository"]).resolve(strict=True)
        if not isinstance(item["issue"], str) or not item["issue"].strip():
            raise ValueError(f"compatibility case has no issue: {case_id}")
        cases.append(
            CompatibilityCase(
                case_id=case_id,
                category=str(item["category"]),
                repository=repository,
                issue=item["issue"],
                required_tools=required,
                ordered_milestones=tuple(item["ordered_milestones"]),
                expected_changed_files=tuple(item["expected_changed_files"]),
                minimum_model_test_calls=int(item["minimum_model_test_calls"]),
                prelude=dict(item["prelude"]) if item["prelude"] is not None else None,
                fault=dict(item["fault"]) if item["fault"] is not None else None,
                completable=item["completable"] is True,
            )
        )
    return raw, cases


def load_profile(path: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version", "profile_id", "profile_version", "track", "frozen_at", "claim_boundary",
        "corpus", "provider", "agent_protocol", "controller", "sandbox", "evaluation",
        "admission_gate", "deadline_diagnostics", "protocol",
    }
    if not isinstance(raw, dict) or set(raw) != allowed:
        raise ValueError("compatibility profile has missing or unknown top-level fields")
    if raw["schema_version"] != 1 or raw["profile_version"] != 1 or raw["track"] != TRACK:
        raise ValueError("unsupported compatibility profile identity or version")
    expected_nested = {
        "corpus": {"path", "corpus_id", "corpus_version", "case_count", "content_hash"},
        "provider": {
            "provider", "endpoint_owner", "endpoint", "endpoint_api", "ollama_version", "model",
            "model_manifest_sha256", "model_blob_sha256", "sdk_timeout_seconds", "sdk_retry_limit",
            "temperature", "random_seed", "repetitions_per_case", "credentials_required",
        },
        "agent_protocol": {
            "system_prompt_source", "system_prompt_sha256", "tool_catalog_version", "tool_catalog_sha256",
            "model_visible_tools", "tool_transport", "retrieval_strategy", "arbitrary_shell",
            "model_selected_test_commands",
        },
        "controller": {
            "max_iterations", "max_repair_cycles", "total_timeout_seconds", "command_timeout_seconds",
            "max_observation_chars", "max_patch_chars", "recovery_policy", "compatibility_bound_rationale",
        },
        "sandbox": {
            "image", "network_mode", "user", "read_only_root_filesystem", "capabilities_dropped",
            "no_new_privileges", "memory", "cpus", "pids_limit", "only_staged_workspace_writable",
            "credentials_forwarded",
        },
        "evaluation": {
            "evaluation_schema_version", "taxonomy_id", "taxonomy_version", "taxonomy_sha256",
            "accepted_terminal_reasons", "malformed_codes", "protocol_success_requires_all_ordered_milestones",
            "prelude_evidence_origin", "fault_injection_origin",
        },
        "admission_gate": {
            "first_valid_tool_call_rate_min", "protocol_complete_success_rate_min", "plan_only_timeout_rate_max",
            "duplicate_mutation_count_max", "unsafe_retry_count_max", "malformed_action_rate_max",
            "successful_finalization_rate_min", "decision_values",
        },
        "deadline_diagnostics": {
            "measure_configured_deadline", "measure_last_controller_checkpoint",
            "measure_provider_call_start_and_completion", "measure_tool_call_start_and_completion",
            "measure_controller_return", "measure_cleanup_completion", "measure_artifact_completion",
            "hard_cancellation_supported",
        },
        "protocol": {
            "benchmark_frozen_before_model_run", "one_attempt_per_case", "post_result_case_changes",
            "post_result_threshold_changes", "model_tuning", "swebench_inputs_forbidden",
        },
    }
    for name, fields in expected_nested.items():
        if not isinstance(raw[name], dict) or set(raw[name]) != fields:
            raise ValueError(f"compatibility profile section {name} has missing or unknown fields")
    root = (project_root or Path(__file__).resolve().parents[3]).resolve()
    corpus_path = (root / raw["corpus"]["path"]).resolve(strict=True)
    _, cases = load_corpus(corpus_path)
    if raw["corpus"]["content_hash"] != corpus_hash(corpus_path.parent) or len(cases) != raw["corpus"]["case_count"]:
        raise ValueError("compatibility corpus hash or count mismatch")
    if raw["agent_protocol"]["system_prompt_sha256"] != _sha(SYSTEM_PROMPT.encode()):
        raise ValueError("compatibility system-prompt hash mismatch")
    if raw["agent_protocol"]["tool_catalog_sha256"] != _tool_catalog_hash():
        raise ValueError("compatibility tool-catalog hash mismatch")
    if tuple(raw["agent_protocol"]["model_visible_tools"]) != tuple(d.name for d in PUBLIC_TOOL_DEFINITIONS):
        raise ValueError("compatibility tool surface is not the canonical six tools")
    if raw["provider"]["model"] != "mistral:7b" or raw["provider"]["repetitions_per_case"] != 1:
        raise ValueError("compatibility provider must preserve the frozen one-shot Mistral configuration")
    if raw["controller"]["total_timeout_seconds"] != 60 or raw["controller"]["max_iterations"] != 8:
        raise ValueError("compatibility controller bounds changed after freeze")
    if tuple(raw["admission_gate"]["decision_values"]) != DECISIONS:
        raise ValueError("compatibility decision values changed")
    raw["content_hash"] = _sha(_canonical(raw).encode())
    raw["corpus_path"] = corpus_path
    raw["cases"] = cases
    raw["run_limits"] = RunLimits(
        max_iterations=raw["controller"]["max_iterations"],
        max_repair_cycles=raw["controller"]["max_repair_cycles"],
        command_timeout_seconds=raw["controller"]["command_timeout_seconds"],
        total_timeout_seconds=raw["controller"]["total_timeout_seconds"],
        max_observation_chars=raw["controller"]["max_observation_chars"],
        max_patch_chars=raw["controller"]["max_patch_chars"],
    )
    raw["recovery_policy"] = RecoveryPolicy.from_mapping(raw["controller"]["recovery_policy"])
    return raw


class _PreludeModel:
    def __init__(self, model: Any, prelude: dict[str, Any] | None):
        self.model = model
        self.prelude = [prelude] if prelude is not None else []

    @property
    def metadata(self) -> dict[str, Any]:
        return {**self.model.metadata, "compatibility_prelude": bool(self.prelude)}

    def next_action(self, **kwargs: Any) -> ModelTurn:
        return self.model.next_action(**{**kwargs, "history": [*self.prelude, *kwargs["history"]]})


class _MeasuredModel:
    def __init__(self, model: Any, recorder: TrajectoryRecorder, clock: Any, operations: list[dict[str, Any]]):
        self.model, self.recorder, self.clock, self.operations = model, recorder, clock, operations

    @property
    def metadata(self) -> dict[str, Any]:
        return self.model.metadata

    def next_action(self, **kwargs: Any) -> ModelTurn:
        started = self.clock()
        try:
            return self.model.next_action(**kwargs)
        finally:
            completed = self.clock()
            item = {"operation": "model", "started_ms": started, "completed_ms": completed, "duration_ms": completed - started}
            self.operations.append(item)
            self.recorder.record("compatibility_operation_timing", **item)


class _MeasuredBackend:
    def __init__(self, backend: ToolBackend, recorder: TrajectoryRecorder, clock: Any, operations: list[dict[str, Any]]):
        self.backend, self.recorder, self.clock, self.operations = backend, recorder, clock, operations

    @property
    def definitions(self):
        return self.backend.definitions

    @property
    def schemas(self):
        return self.backend.schemas

    @property
    def revision(self):
        return self.backend.revision

    @property
    def unnecessary_calls(self):
        return self.backend.unnecessary_calls

    @unnecessary_calls.setter
    def unnecessary_calls(self, value):
        self.backend.unnecessary_calls = value

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        started = self.clock()
        try:
            return self.backend.call(name, arguments)
        finally:
            completed = self.clock()
            item = {"operation": "tool", "tool": name, "started_ms": started, "completed_ms": completed, "duration_ms": completed - started}
            self.operations.append(item)
            self.recorder.record("compatibility_operation_timing", **item)


def _security(sandbox: DockerSandbox) -> dict[str, Any]:
    inspected = json.loads(subprocess.run(["docker", "inspect", sandbox.container_name], check=True, text=True, capture_output=True).stdout)[0]
    host = inspected["HostConfig"]
    mounts = inspected["Mounts"]
    env_names = [item.split("=", 1)[0] for item in inspected["Config"]["Env"]]
    identity = subprocess.run(["docker", "exec", sandbox.container_name, "id", "-u"], text=True, capture_output=True)
    assertions = {
        "non_root": identity.stdout.strip() == "10001",
        "network_none": host["NetworkMode"] == "none",
        "read_only_root": host["ReadonlyRootfs"] is True,
        "capabilities_dropped": host["CapDrop"] == ["ALL"],
        "no_new_privileges": "no-new-privileges" in (host["SecurityOpt"] or []),
        "memory_bounded": int(host["Memory"]) > 0,
        "cpu_bounded": int(host["NanoCpus"]) > 0,
        "pids_bounded": int(host["PidsLimit"]) > 0,
        "only_workspace_bind_mounted": [mount["Destination"] for mount in mounts] == ["/workspace"],
        "workspace_writable": mounts[0]["RW"] is True,
        "credentials_absent": not any(any(term in name.casefold() for term in ("api_key", "token", "secret", "password", "ssh")) for name in env_names),
        "canonical_tools": len(PUBLIC_TOOL_DEFINITIONS) == 6,
    }
    return {"passed": all(assertions.values()), "assertions": assertions, "passed_count": sum(assertions.values()), "total_count": len(assertions)}


def _fault(case: CompatibilityCase, recorder: TrajectoryRecorder) -> FaultRuntime | None:
    if case.fault is None:
        return None
    spec = FaultSpec(**case.fault)
    scenario = ReliabilityScenario(
        scenario_id=case.case_id,
        case_id=case.case_id,
        faults=(spec,),
        correction="none",
        expected={"recoverable": True, "stop_reason": "tests_passed", "public_pass": True, "hidden_pass": True, "taxonomy_labels": []},
    )
    return FaultRuntime(scenario, recorder)


def _subsequence(required: tuple[str, ...], observed: list[str]) -> bool:
    position = 0
    for value in observed:
        if position < len(required) and required[position] == value:
            position += 1
    return position == len(required)


def score_case(case: CompatibilityCase, result: RunResult, events: list[dict[str, Any]], *, runtime: FaultRuntime | None) -> dict[str, Any]:
    model_turns = [event for event in events if event["type"] == "model_turn"]
    model_tools = [event for event in events if event["type"] == "tool_call" and event["payload"].get("origin") == "model"]
    valid_tools = [
        event for event in model_tools
        if (event.get("error") or {}).get("code") not in {"unknown_tool", "invalid_arguments"}
    ]
    action_counts = Counter((event["payload"].get("action") or {}).get("kind", "malformed") for event in model_turns)
    called_names = [str(event["payload"].get("tool")) for event in model_tools]
    model_test_events = [event for event in model_tools if event["payload"].get("tool") == "run_tests"]
    milestones: list[str] = []
    if case.prelude is not None:
        first_action = model_tools[0]["payload"] if model_tools else None
        if first_action is not None and not (
            first_action.get("tool") == case.prelude["tool"]
            and first_action.get("arguments") == case.prelude["arguments"]
        ):
            milestones.append("corrected_action")
    for event in events:
        if event["type"] != "tool_call":
            continue
        origin = event["payload"].get("origin")
        tool = str(event["payload"].get("tool"))
        if origin == "model":
            observation = event["payload"].get("observation") or {}
            if tool == "run_tests":
                milestones.append("passed_model_test" if observation.get("passed") else "failed_model_test")
            milestones.append(tool)
        elif origin == "recovery_reconciliation":
            milestones.append("recovery_reconciliation")
    model_final = action_counts["final"] > 0
    accepted_terminal = result.stop_reason in {"model_final", "tests_passed"}
    if model_final:
        milestones.extend(["model_final", "bounded_stop"])
    if accepted_terminal:
        milestones.append("accepted_terminal")
    required_coverage = all(name in called_names for name in case.required_tools)
    changed_match = tuple(sorted(result.changed_files)) == tuple(sorted(case.expected_changed_files))
    enough_tests = len(model_test_events) >= case.minimum_model_test_calls
    protocol_complete = _subsequence(case.ordered_milestones, milestones) and required_coverage and changed_match and enough_tests
    first_tool = valid_tools[0] if valid_tools else None
    run_started = next(event for event in events if event["type"] == "run_started")
    first_tool_ms = None
    if first_tool is not None:
        start_time = datetime.fromisoformat(run_started["timestamp"])
        first_time = datetime.fromisoformat(first_tool["timestamp"])
        first_tool_ms = (first_time - start_time).total_seconds() * 1000
    plans_before_first = 0
    first_tool_sequence = first_tool["sequence"] if first_tool else 10**9
    for event in model_turns:
        if event["sequence"] < first_tool_sequence and (event["payload"].get("action") or {}).get("kind") == "plan":
            plans_before_first += 1
    errors = [(event.get("error") or {}).get("code") for event in events if event.get("error")]
    repeated_calls = sum(
        1 for left, right in zip(model_tools, model_tools[1:])
        if left["payload"].get("tool") == right["payload"].get("tool")
        and left["payload"].get("arguments") == right["payload"].get("arguments")
        and left.get("workspace_revision_before") == right.get("workspace_revision_before")
    )
    mutation_executions = runtime.mutation_executions if runtime is not None else sum(
        event["payload"].get("tool") == "apply_patch" and event["payload"].get("ok")
        and (event["payload"].get("observation") or {}).get("changed") for event in model_tools
    )
    failure_analysis = classify_failures(events)
    unsafe = sum(bool(item.get("unsafe")) for item in failure_analysis["recovery_decisions"])
    return {
        "protocol_complete": protocol_complete,
        "milestones_observed": milestones,
        "required_tool_coverage": required_coverage,
        "first_valid_tool_call": first_tool is not None,
        "time_to_first_valid_tool_call_ms": first_tool_ms,
        "plan_only_responses_before_first_tool": plans_before_first,
        "model_turns": len(model_turns),
        "action_counts": dict(sorted(action_counts.items())),
        "valid_structured_actions": len(model_turns) - sum(code in {"malformed_output", "empty_output"} for code in errors),
        "malformed_actions": sum(code in {"malformed_output", "empty_output"} for code in errors),
        "unknown_tool_calls": errors.count("unknown_tool"),
        "invalid_argument_calls": errors.count("invalid_arguments"),
        "model_tool_calls": len(model_tools),
        "model_tool_names": called_names,
        "tool_selection_correct_calls": sum(name in case.required_tools for name in called_names),
        "model_test_calls": len(model_test_events),
        "unnecessary_tool_calls": result.unnecessary_tool_calls,
        "repeated_identical_tool_calls": repeated_calls,
        "duplicate_mutation_attempts": max(0, mutation_executions - 1),
        "correction_after_structured_error": "corrected_action" in milestones if case.prelude else None,
        "correction_after_failed_tests": (
            _subsequence(("failed_model_test", "apply_patch", "passed_model_test"), milestones)
            if case.case_id == "sequence-test-repair" else None
        ),
        "state_revision_aware": "git_diff" in called_names if case.case_id == "state-diff-awareness" else None,
        "successful_finalization": accepted_terminal,
        "model_final": model_final,
        "premature_final": model_final and not _subsequence(case.ordered_milestones[:-1], milestones),
        "plan_only_loop": not model_tools and result.stop_reason in {"iteration_limit", "total_timeout"},
        "stop_reason": result.stop_reason,
        "changed_files": result.changed_files,
        "unsafe_retry_count": unsafe,
        "failure_analysis": failure_analysis,
    }


def _aggregate(cases: list[dict[str, Any]], gate: Mapping[str, Any]) -> dict[str, Any]:
    requiring = [case for case in cases if case["required_tools"]]
    completable = [case for case in cases if case["completable"]]
    turns = sum(case["score"]["model_turns"] for case in cases)
    malformed = sum(case["score"]["malformed_actions"] for case in cases)
    tool_calls = sum(case["score"]["model_tool_calls"] for case in cases)
    correct_calls = sum(case["score"]["tool_selection_correct_calls"] for case in cases)
    first_times = [case["score"]["time_to_first_valid_tool_call_ms"] for case in requiring if case["score"]["time_to_first_valid_tool_call_ms"] is not None]

    def usage_total(field: str) -> int | None:
        values = [case["usage"].get(field) for case in cases]
        return None if any(value is None for value in values) else sum(int(value) for value in values)

    aggregate = {
        "cases": len(cases),
        "first_valid_tool_call_rate": sum(case["score"]["first_valid_tool_call"] for case in requiring) / len(requiring),
        "time_to_first_valid_tool_call_ms_mean": sum(first_times) / len(first_times) if first_times else None,
        "plan_only_responses_before_first_tool": sum(case["score"]["plan_only_responses_before_first_tool"] for case in requiring),
        "valid_structured_action_rate": (turns - malformed) / turns if turns else 0.0,
        "malformed_action_rate": malformed / turns if turns else 0.0,
        "unknown_tool_rate": sum(case["score"]["unknown_tool_calls"] for case in cases) / turns if turns else 0.0,
        "invalid_argument_rate": sum(case["score"]["invalid_argument_calls"] for case in cases) / turns if turns else 0.0,
        "tool_selection_accuracy": correct_calls / tool_calls if tool_calls else 0.0,
        "required_tool_coverage_rate": sum(case["score"]["required_tool_coverage"] for case in requiring) / len(requiring),
        "unnecessary_tool_calls": sum(case["score"]["unnecessary_tool_calls"] for case in cases),
        "repeated_identical_tool_calls": sum(case["score"]["repeated_identical_tool_calls"] for case in cases),
        "duplicate_mutation_count": sum(case["score"]["duplicate_mutation_attempts"] for case in cases),
        "correction_after_structured_error_rate": sum(case["score"]["correction_after_structured_error"] is True for case in cases if case["score"]["correction_after_structured_error"] is not None) / max(1, sum(case["score"]["correction_after_structured_error"] is not None for case in cases)),
        "correction_after_failed_tests_rate": sum(case["score"]["correction_after_failed_tests"] is True for case in cases if case["score"]["correction_after_failed_tests"] is not None) / max(1, sum(case["score"]["correction_after_failed_tests"] is not None for case in cases)),
        "successful_finalization_rate": sum(case["score"]["successful_finalization"] for case in completable) / len(completable),
        "premature_final_rate": sum(case["score"]["premature_final"] for case in cases) / len(cases),
        "plan_only_loop_rate": sum(case["score"]["plan_only_loop"] for case in cases) / len(cases),
        "iteration_limit_rate": sum(case["score"]["stop_reason"] == "iteration_limit" for case in cases) / len(cases),
        "total_timeout_rate": sum(case["score"]["stop_reason"] == "total_timeout" for case in cases) / len(cases),
        "protocol_complete_success_rate": sum(case["score"]["protocol_complete"] for case in cases) / len(cases),
        "unsafe_retry_count": sum(case["score"]["unsafe_retry_count"] for case in cases),
        "input_tokens": usage_total("input_tokens"),
        "output_tokens": usage_total("output_tokens"),
        "cached_tokens": usage_total("cached_tokens"),
        "reasoning_tokens": usage_total("reasoning_tokens"),
        "model_latency_ms": sum(case["trace_summary"]["latency_ms"]["model"] for case in cases),
        "tool_latency_ms": sum(case["trace_summary"]["latency_ms"]["tool"] for case in cases),
        "total_latency_ms": sum(case["latency_ms"] for case in cases),
    }
    checks = {
        "first_valid_tool_call_rate": aggregate["first_valid_tool_call_rate"] >= gate["first_valid_tool_call_rate_min"],
        "protocol_complete_success_rate": aggregate["protocol_complete_success_rate"] >= gate["protocol_complete_success_rate_min"],
        "plan_only_timeout_rate": aggregate["plan_only_loop_rate"] <= gate["plan_only_timeout_rate_max"],
        "duplicate_mutation_count": aggregate["duplicate_mutation_count"] <= gate["duplicate_mutation_count_max"],
        "unsafe_retry_count": aggregate["unsafe_retry_count"] <= gate["unsafe_retry_count_max"],
        "malformed_action_rate": aggregate["malformed_action_rate"] <= gate["malformed_action_rate_max"],
        "successful_finalization_rate": aggregate["successful_finalization_rate"] >= gate["successful_finalization_rate_min"],
    }
    aggregate["admission_checks"] = checks
    aggregate["decision"] = "COMPATIBLE" if all(checks.values()) else "NOT COMPATIBLE"
    return aggregate


def _comparison_provider(
    profile: Mapping[str, Any],
    provider_config: ProviderConfig | None,
) -> dict[str, Any]:
    if provider_config is None:
        return dict(profile["provider"])
    provider_config.validate()
    if provider_config.provider != "deepseek" or provider_config.model != "deepseek-v4-pro":
        raise ValueError("the frozen comparison permits only the previously validated deepseek-v4-pro configuration")
    return {
        "provider": "deepseek",
        "endpoint_owner": "official DeepSeek",
        "endpoint": DEEPSEEK_BASE_URL,
        "endpoint_api": "Chat Completions",
        "model": provider_config.model,
        "thinking_mode": "disabled",
        "sdk_timeout_seconds": profile["provider"]["sdk_timeout_seconds"],
        "sdk_retry_limit": profile["provider"]["sdk_retry_limit"],
        "temperature": None,
        "random_seed": None,
        "repetitions_per_case": 1,
        "credentials_required": True,
    }


def _compatibility_model(
    profile: Mapping[str, Any],
    provider_config: ProviderConfig | None,
    *,
    environ: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> tuple[Any, dict[str, Any]]:
    descriptor = _comparison_provider(profile, provider_config)
    if provider_config is None:
        from openai import OpenAI

        provider = profile["provider"]
        compatible_client = client or OpenAI(
            base_url=provider["endpoint"],
            api_key="not-required",
            timeout=provider["sdk_timeout_seconds"],
            max_retries=provider["sdk_retry_limit"],
        )
        return OpenAICompatibleModel(
            provider["model"],
            base_url=provider["endpoint"],
            api_key="not-required",
            client=compatible_client,
        ), descriptor
    environment = os.environ if environ is None else environ
    api_key = environment.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is required for the deepseek provider")
    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=profile["provider"]["sdk_timeout_seconds"],
            max_retries=profile["provider"]["sdk_retry_limit"],
        )
    return create_model(provider_config, environ=environment, client=client), descriptor


def run_compatibility(
    profile_path: Path,
    output: Path,
    *,
    project_root: Path | None = None,
    provider_config: ProviderConfig | None = None,
    environ: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    root = (project_root or Path(__file__).resolve().parents[3]).resolve()
    profile = load_profile(profile_path, project_root=root)
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"compatibility output already exists: {output}")
    output.mkdir(parents=True)
    base_model, execution_provider = _compatibility_model(
        profile,
        provider_config,
        environ=environ,
        client=client,
    )
    case_results: list[dict[str, Any]] = []
    infrastructure_failures: list[dict[str, str]] = []
    for case in profile["cases"]:
        case_dir = output / "runs" / case.case_id
        case_dir.mkdir(parents=True)
        run_id = f"compat-{case.case_id}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        clock = lambda: (time.perf_counter() - started) * 1000
        timing: dict[str, list[float]] = {}
        operations: list[dict[str, Any]] = []
        cleanup_completed = artifact_completed = None
        try:
            with tempfile.TemporaryDirectory(prefix=f"repopilot-compat-{case.case_id}-") as temporary:
                workspace = stage_repository(case.repository, Path(temporary) / "workspace")
                recorder = TrajectoryRecorder(
                    case_dir / "trajectory.jsonl",
                    run_id=run_id,
                    metadata={
                        "issue": case.issue,
                        "repository": "synthetic-staged-workspace",
                        "model": base_model.metadata,
                        "limits": asdict(profile["run_limits"]),
                        "evaluation_profile": {"profile_id": profile["profile_id"], "profile_version": profile["profile_version"], "content_hash": profile["content_hash"]},
                        "compatibility_case": case.case_id,
                        "prelude_evidence": bool(case.prelude),
                        "fault_injection": bool(case.fault),
                    },
                )
                sandbox = DockerSandbox(
                    workspace,
                    test_command=("python", "-m", "pytest", "-q"),
                    command_timeout_seconds=profile["controller"]["command_timeout_seconds"],
                    issue=case.issue,
                    config=SandboxConfig(
                        image=profile["sandbox"]["image"], memory=profile["sandbox"]["memory"],
                        cpus=profile["sandbox"]["cpus"], pids_limit=profile["sandbox"]["pids_limit"],
                    ),
                    retrieval={"strategy": "structural"},
                )
                sandbox.start()
                security = _security(sandbox)
                contamination = {
                    "passed": not any(value in case.issue.casefold() for value in SWE_BENCH_CONTAMINATED_IDS)
                    and not any((workspace / name).exists() for name in ("reference.patch", "test.patch", "gold.patch")),
                    "swebench_instance_ids_absent": not any(value in case.issue.casefold() for value in SWE_BENCH_CONTAMINATED_IDS),
                    "answer_files_absent": not any((workspace / name).exists() for name in ("reference.patch", "test.patch", "gold.patch")),
                }
                if not security["passed"] or not contamination["passed"]:
                    raise RuntimeError("compatibility security or contamination gate failed")
                registry: ToolBackend = ToolRegistry(sandbox)
                runtime = _fault(case, recorder)
                if runtime is not None:
                    registry = FaultInjectingBackend(registry, runtime)
                measured_tools = _MeasuredBackend(registry, recorder, clock, operations)
                model = _MeasuredModel(_PreludeModel(base_model, case.prelude), recorder, clock, operations)

                def hook(name: str, elapsed_ms: float) -> None:
                    timing.setdefault(name, []).append(elapsed_ms)

                result = AgentLoop(
                    issue=case.issue,
                    model=model,
                    tools=measured_tools,
                    recorder=recorder,
                    max_iterations=profile["run_limits"].max_iterations,
                    max_repair_cycles=profile["run_limits"].max_repair_cycles,
                    total_timeout_seconds=profile["run_limits"].total_timeout_seconds,
                    recovery_policy=profile["recovery_policy"],
                    fault_runtime=runtime,
                    timing_hook=hook,
                ).run(run_id, started_at=started)
                controller_return_ms = clock()
                sandbox.close()
                cleanup_completed = clock()
                events = list(TraceReader(case_dir / "trajectory.jsonl"))
                score = score_case(case, result, events, runtime=runtime)
                summary = summarize_trace(case_dir / "trajectory.jsonl")
                case_payload = {
                    "case_id": case.case_id,
                    "category": case.category,
                    "required_tools": list(case.required_tools),
                    "ordered_milestones": list(case.ordered_milestones),
                    "completable": case.completable,
                    "run_id": run_id,
                    "success": result.success,
                    "stop_reason": result.stop_reason,
                    "iterations": result.iterations,
                    "repair_cycles": result.repair_cycles,
                    "tool_calls": result.tool_calls,
                    "unnecessary_tool_calls": result.unnecessary_tool_calls,
                    "latency_ms": result.latency_ms,
                    "usage": asdict(result.usage),
                    "changed_files": result.changed_files,
                    "score": score,
                    "security": security,
                    "contamination": contamination,
                    "trace_summary": summary,
                    "trace_reconciliation": reconcile_summary(summary, {**asdict(result), "usage": asdict(result.usage)}),
                    "deadline_diagnostics": {
                        "configured_deadline_ms": profile["controller"]["total_timeout_seconds"] * 1000,
                        "controller_checkpoints_ms": timing,
                        "provider_and_tool_operations": operations,
                        "deadline_detection_ms": (timing.get("deadline_detected") or [None])[-1],
                        "cancellation_initiation_ms": None,
                        "hard_cancellation_supported": False,
                        "active_operation_completion_ms": max((item["completed_ms"] for item in operations), default=None),
                        "controller_return_ms": controller_return_ms,
                        "cleanup_completion_ms": cleanup_completed,
                        "artifact_completion_ms": None,
                        "process_exit_ms": None,
                    },
                }
                (case_dir / "run.json").write_text(json.dumps(redact_value(case_payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
                artifact_completed = clock()
                case_payload["deadline_diagnostics"]["artifact_completion_ms"] = artifact_completed
                case_payload["deadline_diagnostics"]["process_exit_ms"] = artifact_completed
                (case_dir / "run.json").write_text(json.dumps(redact_value(case_payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
                case_results.append(case_payload)
        except Exception as exc:
            infrastructure_failures.append({"case_id": case.case_id, "error": f"{type(exc).__name__}: {exc}"})
            break
    aggregate = _aggregate(case_results, profile["admission_gate"]) if case_results else {"decision": "INCONCLUSIVE / INFRASTRUCTURE FAILURE"}
    if infrastructure_failures or len(case_results) != len(profile["cases"]):
        aggregate["decision"] = "INCONCLUSIVE / INFRASTRUCTURE FAILURE"
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "track": TRACK,
        "claim_boundary": profile["claim_boundary"],
        "profile": {
            "profile_id": profile["profile_id"], "profile_version": profile["profile_version"],
            "content_hash": profile["content_hash"], "corpus_hash": profile["corpus"]["content_hash"],
        },
        "provider": execution_provider,
        "admission_gate": profile["admission_gate"],
        "cases": case_results,
        "aggregate": aggregate,
        "compatibility_decision": aggregate["decision"],
        "infrastructure_failures": infrastructure_failures,
        "diagnosis": {
            "observed_evidence": [],
            "manual_hypotheses": [],
            "classification": "pending post-run evidence review",
        },
        "deadline_diagnosis": {
            "observed_evidence": [],
            "cancellation_supported": False,
            "hard_deadline_claim": False,
        },
        "comparison_context": {
            "frozen_baseline_provider": profile["provider"],
            "execution_provider": execution_provider,
            "frozen_profile_unchanged": True,
        },
        "known_limitations": [
            "Twelve synthetic cases measure protocol interoperability, not coding ability.",
            "One repetition does not measure variance.",
            "Prelude and fault evidence are deterministic fixtures, not naturally model-generated errors.",
        ],
    }
    (output / "MODEL_COMPATIBILITY_GATE.json").write_text(json.dumps(redact_value(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen RepoPilot model/controller compatibility gate")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--provider", choices=("deepseek",))
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    if (args.provider is None) != (args.model is None):
        parser.error("--provider and --model must be supplied together")
    provider_config = ProviderConfig(provider=args.provider, model=args.model) if args.provider else None
    report = run_compatibility(
        args.profile.resolve(),
        args.output.resolve(),
        project_root=args.project_root,
        provider_config=provider_config,
    )
    print(json.dumps({"decision": report["compatibility_decision"], "aggregate": report["aggregate"]}, indent=2, sort_keys=True))
    return 0 if report["compatibility_decision"] == "COMPATIBLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
