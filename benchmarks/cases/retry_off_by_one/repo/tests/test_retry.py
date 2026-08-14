import pytest

from retry import run_with_retries


def test_success_on_first_attempt():
    assert run_with_retries(lambda: "ok", retries=2) == "ok"


def test_negative_retries_are_rejected():
    with pytest.raises(ValueError, match="negative"):
        run_with_retries(lambda: "never", retries=-1)
