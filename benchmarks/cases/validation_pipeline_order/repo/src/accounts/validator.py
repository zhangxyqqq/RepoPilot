def validate_nickname(value: str | None) -> None:
    if value is None:
        return
    if not value.strip():
        raise ValueError("nickname cannot be blank")
    if len(value) < 3:
        raise ValueError("nickname must contain at least three characters")
