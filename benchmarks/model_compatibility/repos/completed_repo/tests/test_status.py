from status import current_status


def test_status() -> None:
    assert current_status() == "complete"
