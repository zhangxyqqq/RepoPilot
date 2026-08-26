from __future__ import annotations

from repopilot.agent.recovery import RecoveryPolicy, failure_from_exception
from repopilot.models import FailureMetadata


def _failure(code, retryable, state, operation):
    return FailureMetadata(code, code, retryable, state, operation)


def _decide(failure, *, budget=1, deadline=1000, tool=None):
    return RecoveryPolicy(max_test_timeout_retries=1).decide(
        failure,
        attempt=1,
        remaining_deadline_ms=deadline,
        remaining_budget=budget,
        revision_before=0,
        revision_after=0,
        tool_name=tool,
    )


def test_default_policy_allows_only_bounded_safe_replays() -> None:
    assert _decide(_failure("provider_error", True, "pre_execution", "model")).action == "retry_model"
    assert _decide(_failure("timeout", True, "pre_execution", "safe_read"), tool="read_file").action == "retry_read_only"
    assert _decide(_failure("timeout", True, "post_execution", "bounded_test"), tool="run_tests").action == "rerun_tests"


def test_policy_never_replays_invalid_policy_or_patch_failures() -> None:
    assert _decide(_failure("invalid_arguments", False, "pre_execution", "safe_read"), tool="read_file").action == "return_to_model"
    assert _decide(_failure("patch_rejected", False, "pre_execution", "non_idempotent_mutation"), tool="apply_patch").action == "return_to_model"
    assert _decide(_failure("policy_rejected", False, "pre_execution", "policy"), tool="apply_patch").action == "return_to_model"


def test_ambiguous_mutation_reconciles_and_never_replays() -> None:
    decision = _decide(
        _failure("response_lost", False, "ambiguous_execution", "non_idempotent_mutation"),
        tool="apply_patch",
    )
    assert decision.action == "reconcile_mutation"
    assert "never blindly replayed" in decision.reason


def test_deadline_and_retry_budgets_fail_closed() -> None:
    failure = _failure("provider_error", True, "pre_execution", "model")
    assert _decide(failure, deadline=0).action == "stop_total_timeout"
    assert _decide(failure, budget=0).action == "stop"


def test_provider_exception_normalization_is_conservative_and_stable() -> None:
    class RateLimitError(RuntimeError):
        status_code = 429

    retryable = failure_from_exception(RateLimitError("slow down"))
    assert (retryable.code, retryable.retryable, retryable.execution_state) == (
        "provider_error", True, "pre_execution"
    )
    permanent = failure_from_exception(RuntimeError("bad request"))
    assert permanent.retryable is False
    malformed = failure_from_exception(ValueError("model returned invalid JSON arguments for read_file"))
    assert (malformed.code, malformed.retryable, malformed.execution_state) == (
        "malformed_output", False, "post_execution"
    )
