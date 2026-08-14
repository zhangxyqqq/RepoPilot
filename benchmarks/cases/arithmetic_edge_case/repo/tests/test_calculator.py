import pytest

from calculator import safe_divide


def test_positive_division():
    assert safe_divide(12, 3) == 4


def test_zero_is_rejected():
    with pytest.raises(ValueError, match="non-zero"):
        safe_divide(3, 0)
