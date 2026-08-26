from feature import is_enabled


def test_enabled() -> None:
    assert is_enabled() is True
