from pathlib import PurePosixPath

import pytest

from repopilot.sandbox.policy import validate_relative_path, validate_test_command


def test_relative_paths_are_accepted():
    assert validate_relative_path("src/module.py") == PurePosixPath("src/module.py")


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "src/../../secret", ".env"])
def test_workspace_escape_and_sensitive_paths_are_rejected(path):
    with pytest.raises(ValueError):
        validate_relative_path(path)


def test_only_fixed_pytest_command_is_allowed():
    validate_test_command(("python", "-m", "pytest", "-q"))
    with pytest.raises(ValueError):
        validate_test_command(("sh", "-c", "pytest"))
