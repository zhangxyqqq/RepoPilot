from config import configured_level


def test_level_is_safe() -> None:
    assert configured_level() == "safe"
