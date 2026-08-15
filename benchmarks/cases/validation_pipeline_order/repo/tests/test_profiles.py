import pytest

from accounts.service import build_profile


def test_whitespace_only_optional_nickname_is_absent():
    assert build_profile({"nickname": "   "}) == {"nickname": None}


def test_short_nonblank_nickname_is_rejected():
    with pytest.raises(ValueError, match="three"):
        build_profile({"nickname": "ab"})
