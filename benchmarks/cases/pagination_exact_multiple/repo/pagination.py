def count_pages(total: int, page_size: int) -> int:
    """Return the number of pages needed for a result set."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if total < 0:
        raise ValueError("total cannot be negative")
    if total == 0:
        return 0
    return total // page_size + 1
