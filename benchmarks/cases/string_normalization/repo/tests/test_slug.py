from slug import slugify


def test_simple_words():
    assert slugify("Hello World") == "hello-world"


def test_lowercase_is_preserved():
    assert slugify("ready") == "ready"
