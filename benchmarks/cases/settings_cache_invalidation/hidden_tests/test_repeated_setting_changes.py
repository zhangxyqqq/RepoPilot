from client.http import HttpClient
from client.settings import Settings


def test_endpoint_updates_after_every_host_change():
    settings = Settings("first.example")
    assert settings.endpoint == "https://first.example/api"
    settings.host = "second.example"
    assert settings.endpoint == "https://second.example/api"
    settings.host = "third.example/"
    assert settings.endpoint == "https://third.example/api"


def test_client_observes_change_after_endpoint_was_cached():
    settings = Settings("before.example")
    client = HttpClient(settings)
    assert client.request_url("items") == "https://before.example/api/items"
    settings.host = "after.example"
    assert client.request_url("items") == "https://after.example/api/items"
