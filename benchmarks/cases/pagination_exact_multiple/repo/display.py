def page_label(current: int, total_pages: int) -> str:
    """Format already-computed page metadata for display."""
    if total_pages == 0:
        return "No results"
    return f"Page {current} of {total_pages}"
