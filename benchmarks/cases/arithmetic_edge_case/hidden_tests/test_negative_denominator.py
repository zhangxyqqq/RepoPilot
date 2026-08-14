from calculator import safe_divide


def test_negative_denominator_is_valid():
    assert safe_divide(12, -3) == -4
