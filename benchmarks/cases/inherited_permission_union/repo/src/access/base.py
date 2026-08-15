class BasePolicy:
    def __init__(self, base_permissions: set[str]):
        self.base_permissions = set(base_permissions)

    def permissions(self) -> set[str]:
        return set(self.base_permissions)
