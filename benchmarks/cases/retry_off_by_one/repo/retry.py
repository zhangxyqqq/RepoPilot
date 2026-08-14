from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def run_with_retries(operation: Callable[[], T], retries: int) -> T:
    """Run an operation with the requested number of additional attempts."""
    if retries < 0:
        raise ValueError("retries cannot be negative")
    last_error: Exception | None = None
    for _attempt in range(retries):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error
