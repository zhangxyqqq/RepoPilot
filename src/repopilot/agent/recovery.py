from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from repopilot.models import FailureMetadata


RecoveryAction = Literal[
    "retry_model",
    "retry_read_only",
    "rerun_tests",
    "reconcile_mutation",
    "return_to_model",
    "stop",
    "stop_total_timeout",
]


@dataclass(frozen=True)
class RecoveryPolicy:
    version: int = 1
    max_model_retries: int = 2
    max_read_only_retries: int = 1
    max_test_timeout_retries: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "RecoveryPolicy":
        if value is None:
            return cls()
        allowed = {"version", "max_model_retries", "max_read_only_retries", "max_test_timeout_retries"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown recovery policy field: {unknown[0]}")
        resolved: dict[str, int] = {
            "version": int(value.get("version", 1)),
            "max_model_retries": int(value.get("max_model_retries", 2)),
            "max_read_only_retries": int(value.get("max_read_only_retries", 1)),
            "max_test_timeout_retries": int(value.get("max_test_timeout_retries", 0)),
        }
        if resolved["version"] != 1:
            raise ValueError("unsupported recovery policy version")
        for key in ("max_model_retries", "max_read_only_retries", "max_test_timeout_retries"):
            if isinstance(value.get(key), bool) or resolved[key] < 0 or resolved[key] > 10:
                raise ValueError(f"{key} must be an integer between 0 and 10")
        return cls(**resolved)

    def decide(
        self,
        failure: FailureMetadata,
        *,
        attempt: int,
        remaining_deadline_ms: float,
        remaining_budget: int,
        revision_before: int,
        revision_after: int,
        tool_name: str | None = None,
    ) -> "RecoveryDecision":
        if remaining_deadline_ms <= 0:
            action: RecoveryAction = "stop_total_timeout"
            reason = "total wall-clock deadline exhausted"
        elif failure.operation_class == "policy" or failure.code in {
            "policy_rejected", "invalid_arguments", "patch_rejected",
        }:
            action = "return_to_model"
            reason = "automatic replay is forbidden for policy, validation, or patch rejection"
        elif failure.operation_class == "model":
            if failure.code != "provider_error":
                action = "return_to_model"
                reason = "malformed model evidence is returned to the next normal reasoning iteration"
            elif failure.retryable and failure.execution_state == "pre_execution" and remaining_budget > 0:
                action = "retry_model"
                reason = "retryable provider failure occurred before a model turn"
            else:
                action = "stop"
                reason = "model failure is non-retryable or its separate retry budget is exhausted"
        elif failure.operation_class == "non_idempotent_mutation":
            if failure.execution_state == "ambiguous_execution":
                action = "reconcile_mutation"
                reason = "ambiguous mutation must be reconciled and never blindly replayed"
            else:
                action = "return_to_model"
                reason = "mutating operation is not automatically replayed"
        elif tool_name == "run_tests" and failure.code == "timeout":
            if remaining_budget > 0:
                action = "rerun_tests"
                reason = "profile permits one bounded test-timeout rerun"
            else:
                action = "return_to_model"
                reason = "test-timeout rerun budget exhausted"
        elif (
            failure.operation_class == "safe_read"
            and failure.retryable
            and failure.execution_state == "pre_execution"
            and remaining_budget > 0
        ):
            action = "retry_read_only"
            reason = "known pre-execution read failure is safe for one bounded retry"
        else:
            action = "return_to_model"
            reason = "failure evidence is returned without automatic replay"
        return RecoveryDecision(
            policy_version=self.version,
            action=action,
            reason=reason,
            attempt=attempt,
            remaining_retry_budget=max(0, remaining_budget),
            remaining_deadline_ms=max(0.0, remaining_deadline_ms),
            revision_before=revision_before,
            revision_after=revision_after,
            retryable=failure.retryable,
            execution_state=failure.execution_state,
            operation_class=failure.operation_class,
            fault_type=failure.fault_type,
            injected=failure.injected,
            unsafe=False,
        )


@dataclass(frozen=True)
class RecoveryDecision:
    policy_version: int
    action: RecoveryAction
    reason: str
    attempt: int
    remaining_retry_budget: int
    remaining_deadline_ms: float
    revision_before: int
    revision_after: int
    retryable: bool
    execution_state: str
    operation_class: str
    fault_type: str | None
    injected: bool
    unsafe: bool


class ModelBoundaryError(RuntimeError):
    def __init__(self, failure: FailureMetadata):
        super().__init__(failure.message)
        self.failure = failure


def failure_from_exception(exc: Exception) -> FailureMetadata:
    if isinstance(exc, ModelBoundaryError):
        return exc.failure
    message = f"{type(exc).__name__}: {exc}"
    lowered = message.lower()
    if isinstance(exc, ValueError) and any(
        signal in lowered
        for signal in ("invalid json arguments", "non-object arguments", "no chat completion choices")
    ):
        return FailureMetadata(
            code="malformed_output",
            message=message,
            retryable=False,
            execution_state="post_execution",
            operation_class="model",
        )
    status_code = getattr(exc, "status_code", None)
    retryable_status = isinstance(status_code, int) and (
        status_code in {408, 409, 429} or status_code >= 500
    )
    retryable_transport = any(signal in type(exc).__name__.lower() for signal in ("timeout", "connection"))
    return FailureMetadata(
        code="provider_error",
        message=message,
        retryable=bool(retryable_status or retryable_transport),
        execution_state="pre_execution",
        operation_class="model",
    )
