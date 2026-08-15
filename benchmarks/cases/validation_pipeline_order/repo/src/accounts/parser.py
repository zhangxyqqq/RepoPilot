from collections.abc import Mapping


def parse_nickname(payload: Mapping[str, object]) -> str | None:
    value = payload.get("nickname")
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("nickname must be text")
    return value
