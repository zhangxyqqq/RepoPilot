from access.project import ProjectPolicy


def test_multiple_inherited_and_project_permissions_are_combined():
    policy = ProjectPolicy({"read", "comment"}, {"deploy", "archive"})
    assert policy.permissions() == {"read", "comment", "deploy", "archive"}


def test_duplicate_permission_is_naturally_deduplicated():
    policy = ProjectPolicy({"read", "deploy"}, {"deploy"})
    assert policy.permissions() == {"read", "deploy"}


def test_empty_project_permissions_still_preserve_base():
    policy = ProjectPolicy({"read"}, set())
    assert policy.permissions() == {"read"}
