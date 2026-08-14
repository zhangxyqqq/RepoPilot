from slug import slugify


def test_surrounding_and_repeated_whitespace():
    assert slugify("  Hello   Wide World  ") == "hello-wide-world"


def test_non_space_whitespace():
    assert slugify("hello\tworld\nagain") == "hello-world-again"
