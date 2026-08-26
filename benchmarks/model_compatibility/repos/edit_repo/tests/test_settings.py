from settings import current_mode


def test_mode_is_new() -> None:
    assert current_mode() == "new"
