from access.base import BasePolicy


class RolePolicy(BasePolicy):
    """A sibling implementation that already preserves inherited permissions."""

    def __init__(self, base_permissions: set[str], role_permissions: set[str]):
        super().__init__(base_permissions)
        self.role_permissions = set(role_permissions)

    def permissions(self) -> set[str]:
        return super().permissions() | self.role_permissions
