from access.base import BasePolicy


class ProjectPolicy(BasePolicy):
    def __init__(self, base_permissions: set[str], project_permissions: set[str]):
        super().__init__(base_permissions)
        self.project_permissions = set(project_permissions)

    def permissions(self) -> set[str]:
        return set(self.project_permissions)
