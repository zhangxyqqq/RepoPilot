from config import resolve_config


def test_file_overrides_defaults():
    assert resolve_config({"port": "8000"}, {"port": "9000"}, {})["port"] == "9000"


def test_environment_adds_values():
    assert resolve_config({}, {}, {"debug": "true"})["debug"] == "true"
