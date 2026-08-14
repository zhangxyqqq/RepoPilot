from retry import run_with_retries


def test_two_retries_allow_three_calls():
    calls = 0

    def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return "ok"

    assert run_with_retries(flaky, retries=2) == "ok"
    assert calls == 3


def test_zero_retries_still_calls_once():
    assert run_with_retries(lambda: "ok", retries=0) == "ok"
