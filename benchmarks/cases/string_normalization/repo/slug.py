def slugify(value: str) -> str:
    """Return a lowercase, hyphen-separated slug."""
    return value.lower().replace(" ", "-")
