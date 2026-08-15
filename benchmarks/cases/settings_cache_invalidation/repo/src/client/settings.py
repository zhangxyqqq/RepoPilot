from client.url import normalize_host


class Settings:
    def __init__(self, host: str):
        self._host = normalize_host(host)
        self._endpoint_cache: str | None = None

    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        self._host = normalize_host(value)

    @property
    def endpoint(self) -> str:
        if self._endpoint_cache is None:
            self._endpoint_cache = f"https://{self._host}/api"
        return self._endpoint_cache
