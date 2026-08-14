from config import resolve_config


def test_environment_overrides_file_and_default():
    result = resolve_config(
        {"endpoint": "default"},
        {"endpoint": "file"},
        {"endpoint": "environment"},
    )
    assert result["endpoint"] == "environment"
