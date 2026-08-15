from access.project import ProjectPolicy
from access.service import is_allowed


def test_project_policy_keeps_inherited_permission():
    policy = ProjectPolicy({"read"}, {"deploy"})
    assert is_allowed(policy, "read")


def test_project_permission_remains_available():
    policy = ProjectPolicy({"read"}, {"deploy"})
    assert is_allowed(policy, "deploy")
