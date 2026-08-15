class ResponseCache:
    """A plausible but unrelated cache used for response bodies."""

    def __init__(self):
        self._values: dict[str, str] = {}

    def clear(self) -> None:
        self._values.clear()
