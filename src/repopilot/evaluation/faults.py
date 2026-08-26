from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from repopilot.agent.recovery import ModelBoundaryError
from repopilot.llm.base import ModelClient
from repopilot.models import AgentAction, FailureMetadata, ModelTurn, TokenUsage, ToolResult
from repopilot.tools import ToolBackend
from repopilot.tools.definitions import PUBLIC_TOOL_BY_NAME
from repopilot.trajectory import TrajectoryRecorder


FAULT_SCHEMA_VERSION = 1
MODEL_FAULTS = {"transient_provider_exception", "permanent_provider_exception", "malformed_action", "empty_response"}
TOOL_FAULTS = {
    "validation_error", "pre_execution_timeout", "response_loss_before_execution",
    "ambiguous_response_loss_after_execution", "patch_rejection", "read_timeout",
    "public_test_failure", "test_timeout",
}
CONTROLLER_FAULTS = {
    "near_deadline", "iteration_budget_exhausted", "repair_budget_exhausted", "retry_budget_exhausted",
}
_BOUNDARY_FAULTS = {"model": MODEL_FAULTS, "tool": TOOL_FAULTS, "controller": CONTROLLER_FAULTS}


class FaultScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class FaultSpec:
    boundary: str
    fault_type: str
    occurrence: int
    tool: str | None = None
    checkpoint: str | None = None
    exhaust_deadline: bool = False


@dataclass(frozen=True)
class ReliabilityScenario:
    scenario_id: str
    case_id: str
    faults: tuple[FaultSpec, ...]
    correction: str
    expected: dict[str, Any]


@dataclass(frozen=True)
class FaultSchedule:
    schedule_id: str
    version: int
    seed: int
    scenarios: tuple[ReliabilityScenario, ...]
    content_hash: str
    source: str

    def artifact(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "version": self.version,
            "schema_version": FAULT_SCHEMA_VERSION,
            "seed": self.seed,
            "content_hash": self.content_hash,
            "source": self.source,
        }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FaultScheduleError(f"duplicate fault schedule field: {key}")
        result[key] = value
    return result


def load_fault_schedule(path: Path) -> FaultSchedule:
    source = path.resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise FaultScheduleError(f"cannot load fault schedule {source}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "schedule_id", "version", "seed", "scenarios"}:
        raise FaultScheduleError("fault schedule has missing or unknown top-level fields")
    if raw["schema_version"] != 1 or raw["version"] != 1:
        raise FaultScheduleError("unsupported fault schedule version")
    if not isinstance(raw["schedule_id"], str) or not raw["schedule_id"].replace("-", "").isalnum():
        raise FaultScheduleError("invalid schedule_id")
    if isinstance(raw["seed"], bool) or not isinstance(raw["seed"], int) or raw["seed"] < 0:
        raise FaultScheduleError("seed must be a non-negative integer")
    if not isinstance(raw["scenarios"], list) or not raw["scenarios"]:
        raise FaultScheduleError("scenarios must be a non-empty array")
    scenarios: list[ReliabilityScenario] = []
    seen: set[str] = set()
    for item in raw["scenarios"]:
        if not isinstance(item, dict) or set(item) != {"scenario_id", "case_id", "faults", "correction", "expected"}:
            raise FaultScheduleError("scenario has missing or unknown fields")
        scenario_id = item["scenario_id"]
        if not isinstance(scenario_id, str) or scenario_id in seen:
            raise FaultScheduleError(f"duplicate or invalid scenario_id: {scenario_id!r}")
        seen.add(scenario_id)
        if item["correction"] not in {"none", "repeat_faulted_action"}:
            raise FaultScheduleError(f"invalid correction for {scenario_id}")
        faults: list[FaultSpec] = []
        if not isinstance(item["faults"], list) or not item["faults"]:
            raise FaultScheduleError(f"scenario {scenario_id} requires faults")
        for fault in item["faults"]:
            allowed = {"boundary", "fault_type", "occurrence", "tool", "checkpoint", "exhaust_deadline"}
            if not isinstance(fault, dict) or set(fault) - allowed or not {"boundary", "fault_type", "occurrence"} <= set(fault):
                raise FaultScheduleError(f"invalid fault fields in {scenario_id}")
            boundary = fault["boundary"]
            fault_type = fault["fault_type"]
            if boundary not in _BOUNDARY_FAULTS or fault_type not in _BOUNDARY_FAULTS[boundary]:
                raise FaultScheduleError(f"fault {fault_type!r} is invalid at {boundary!r}")
            occurrence = fault["occurrence"]
            if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 1:
                raise FaultScheduleError("fault occurrence must be a positive integer")
            tool = fault.get("tool")
            if boundary == "tool" and not isinstance(tool, str):
                raise FaultScheduleError("tool boundary faults require a canonical tool name")
            if boundary == "tool" and tool not in PUBLIC_TOOL_BY_NAME:
                raise FaultScheduleError(f"fault references unknown tool: {tool}")
            checkpoint = fault.get("checkpoint")
            if boundary == "controller" and checkpoint not in {"before_iteration", "before_recovery"}:
                raise FaultScheduleError("controller faults require a supported checkpoint")
            faults.append(FaultSpec(boundary, fault_type, occurrence, tool, checkpoint, bool(fault.get("exhaust_deadline", False))))
        expected = item["expected"]
        expected_fields = {"recoverable", "stop_reason", "public_pass", "hidden_pass", "taxonomy_labels"}
        if not isinstance(expected, dict) or set(expected) != expected_fields or not isinstance(expected["taxonomy_labels"], list):
            raise FaultScheduleError(f"invalid expected result for {scenario_id}")
        scenarios.append(ReliabilityScenario(scenario_id, str(item["case_id"]), tuple(faults), item["correction"], dict(expected)))
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return FaultSchedule(
        str(raw["schedule_id"]), 1, int(raw["seed"]), tuple(scenarios),
        "sha256:" + hashlib.sha256(canonical).hexdigest(), str(source),
    )


class FaultRuntime:
    def __init__(self, scenario: ReliabilityScenario, recorder: TrajectoryRecorder):
        self.scenario = scenario
        self.recorder = recorder
        self._counts: dict[tuple[str, str], int] = {}
        self.injected_sequence: list[str] = []
        self.mutation_executions = 0

    def _take(self, boundary: str, key: str, *, checkpoint: str | None = None) -> FaultSpec | None:
        counter_key = (boundary, key)
        occurrence = self._counts.get(counter_key, 0) + 1
        self._counts[counter_key] = occurrence
        for fault in self.scenario.faults:
            if fault.boundary != boundary or fault.occurrence != occurrence:
                continue
            if boundary == "tool" and fault.tool != key:
                continue
            if boundary == "controller" and fault.checkpoint != checkpoint:
                continue
            self.injected_sequence.append(fault.fault_type)
            self.recorder.record(
                "fault_injected",
                injected=True,
                scenario_id=self.scenario.scenario_id,
                boundary=boundary,
                fault_type=fault.fault_type,
                occurrence=occurrence,
                tool=fault.tool,
                checkpoint=fault.checkpoint,
                exhaust_deadline=fault.exhaust_deadline,
            )
            return fault
        return None

    def take_model(self) -> FaultSpec | None:
        return self._take("model", "model")

    def take_tool(self, name: str) -> FaultSpec | None:
        return self._take("tool", name)

    def take_controller(self, checkpoint: str) -> FaultSpec | None:
        return self._take("controller", checkpoint, checkpoint=checkpoint)


class FaultInjectingModel:
    def __init__(self, model: ModelClient, runtime: FaultRuntime):
        self.model = model
        self.runtime = runtime

    @property
    def metadata(self) -> dict[str, Any]:
        return {**self.model.metadata, "fault_injection": True, "scenario_id": self.runtime.scenario.scenario_id}

    def next_action(self, **kwargs: Any) -> ModelTurn:
        fault = self.runtime.take_model()
        if fault is None:
            return self.model.next_action(**kwargs)
        if fault.fault_type in {"transient_provider_exception", "permanent_provider_exception"}:
            retryable = fault.fault_type == "transient_provider_exception"
            raise ModelBoundaryError(FailureMetadata(
                code="provider_error",
                message=f"injected {fault.fault_type}",
                retryable=retryable,
                execution_state="pre_execution",
                operation_class="model",
                injected=True,
                fault_type=fault.fault_type,
            ))
        failure = FailureMetadata(
            code="malformed_output" if fault.fault_type == "malformed_action" else "empty_output",
            message=f"injected {fault.fault_type}",
            retryable=False,
            execution_state="post_execution",
            operation_class="model",
            injected=True,
            fault_type=fault.fault_type,
        )
        action = AgentAction("invalid", content="") if fault.fault_type == "malformed_action" else AgentAction("plan", content="")  # type: ignore[arg-type]
        return ModelTurn(action, latency_ms=0.0, usage=TokenUsage(), failure=failure)


class FaultInjectingBackend:
    def __init__(self, backend: ToolBackend, runtime: FaultRuntime):
        self.backend = backend
        self.runtime = runtime

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

    def _failure(self, code: str, message: str, *, retryable: bool, state: str, operation: str, fault: FaultSpec) -> ToolResult:
        failure = FailureMetadata(code, message, retryable, state, operation, True, fault.fault_type)  # type: ignore[arg-type]
        return ToolResult(False, {}, 0.0, self.revision, message, failure)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        fault = self.runtime.take_tool(name)
        if fault is None:
            result = self.backend.call(name, arguments)
            if name == "apply_patch" and result.ok and result.observation.get("changed"):
                self.runtime.mutation_executions += 1
            return result
        operation = next(definition.idempotency for definition in self.definitions if definition.name == name)
        message = f"injected {fault.fault_type}"
        if fault.fault_type == "validation_error":
            return self._failure("invalid_arguments", message, retryable=False, state="pre_execution", operation=operation, fault=fault)
        if fault.fault_type in {"pre_execution_timeout", "read_timeout", "response_loss_before_execution"}:
            code = "timeout" if "timeout" in fault.fault_type else "response_lost"
            return self._failure(code, message, retryable=True, state="pre_execution", operation=operation, fault=fault)
        if fault.fault_type == "patch_rejection":
            return self._failure("patch_rejected", message, retryable=False, state="pre_execution", operation=operation, fault=fault)
        if fault.fault_type == "ambiguous_response_loss_after_execution":
            executed = self.backend.call(name, arguments)
            if name == "apply_patch" and executed.ok and executed.observation.get("changed"):
                self.runtime.mutation_executions += 1
            failure = FailureMetadata("response_lost", message, False, "ambiguous_execution", operation, True, fault.fault_type)  # type: ignore[arg-type]
            return ToolResult(False, {}, 0.0, executed.revision, message, failure)
        if fault.fault_type == "public_test_failure":
            return ToolResult(True, {
                "passed": False,
                "exit_code": 1,
                "output": "injected public failure",
                "timed_out": False,
                "injected": True,
                "fault_type": fault.fault_type,
            }, 0.0, self.revision)
        if fault.fault_type == "test_timeout":
            failure = FailureMetadata("timeout", message, True, "post_execution", "bounded_test", True, fault.fault_type)
            return ToolResult(True, {"passed": False, "exit_code": None, "output": message, "timed_out": True}, 0.0, self.revision, message, failure)
        raise AssertionError(f"unhandled fault type: {fault.fault_type}")
