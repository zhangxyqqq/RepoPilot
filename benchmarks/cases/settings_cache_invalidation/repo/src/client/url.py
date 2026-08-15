def normalize_host(value: str) -> str:
    host = value.strip().rstrip("/")
    if not host:
        raise ValueError("host cannot be empty")
    return host
