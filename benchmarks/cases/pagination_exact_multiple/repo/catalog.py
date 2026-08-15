from pagination import count_pages


def catalog_summary(item_count: int, page_size: int) -> dict[str, int]:
    """Build pagination metadata consumed by the catalog view."""
    return {
        "items": item_count,
        "page_size": page_size,
        "pages": count_pages(item_count, page_size),
    }
