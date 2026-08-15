def parse_flag(value: str) -> bool:
    """Parse configuration text; false values here are handled correctly."""
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid feature flag: {value}")
