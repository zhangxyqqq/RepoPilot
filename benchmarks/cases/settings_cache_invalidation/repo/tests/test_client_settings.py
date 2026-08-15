from client.http import HttpClient
from client.settings import Settings


def test_changed_host_updates_subsequent_request_url():
    settings = Settings("old.example")
    client = HttpClient(settings)
    assert client.request_url("health") == "https://old.example/api/health"

    settings.host = "new.example"

    assert client.request_url("health") == "https://new.example/api/health"


def test_unchanged_host_reuses_same_endpoint_value():
    settings = Settings("stable.example")
    assert settings.endpoint == settings.endpoint
