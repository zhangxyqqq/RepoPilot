from collections.abc import Mapping

from accounts.normalizer import normalize_optional
from accounts.parser import parse_nickname
from accounts.validator import validate_nickname


def build_profile(payload: Mapping[str, object]) -> dict[str, str | None]:
    raw_nickname = parse_nickname(payload)
    validate_nickname(raw_nickname)
    nickname = normalize_optional(raw_nickname)
    return {"nickname": nickname}
