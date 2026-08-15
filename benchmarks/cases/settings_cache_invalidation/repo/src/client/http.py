from client.settings import Settings


class HttpClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def request_url(self, path: str) -> str:
        return f"{self.settings.endpoint}/{path.lstrip('/')}"
