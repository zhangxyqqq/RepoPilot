from catalog import catalog_summary


def test_exact_multiple_has_no_empty_page():
    assert catalog_summary(20, 10)["pages"] == 2


def test_partial_page_is_counted():
    assert catalog_summary(21, 10)["pages"] == 3
