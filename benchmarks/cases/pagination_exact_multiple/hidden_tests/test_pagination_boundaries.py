from catalog import catalog_summary
from pagination import count_pages


def test_single_full_page():
    assert catalog_summary(10, 10)["pages"] == 1


def test_empty_catalog_has_no_pages():
    assert count_pages(0, 10) == 0


def test_large_exact_multiple():
    assert count_pages(100, 25) == 4
