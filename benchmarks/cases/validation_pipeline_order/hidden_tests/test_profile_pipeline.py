import pytest

from accounts.service import build_profile


def test_other_whitespace_is_treated_as_absent():
    assert build_profile({"nickname": "\t\n"}) == {"nickname": None}


def test_valid_nickname_is_trimmed_before_returning():
    assert build_profile({"nickname": "  ada  "}) == {"nickname": "ada"}


def test_trimmed_short_nickname_is_still_rejected():
    with pytest.raises(ValueError, match="three"):
        build_profile({"nickname": "  ab  "})
