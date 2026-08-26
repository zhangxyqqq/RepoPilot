from catalog import named_symbol


def test_named_symbol() -> None:
    assert named_symbol() == "ready"
