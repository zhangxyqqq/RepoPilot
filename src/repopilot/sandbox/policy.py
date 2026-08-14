from __future__ import annotations

from pathlib import PurePosixPath


ALLOWED_TEST_COMMANDS = {
    ("python", "-m", "pytest", "-q"),
}

SENSITIVE_NAMES = {
    ".git",
    ".aws",
    ".azure",
    ".ssh",
    ".gnupg",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}


def is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return name in SENSITIVE_NAMES or lowered == ".env" or lowered.startswith(".env.")


def validate_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value or ".")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must remain inside the repository workspace")
    if any(is_sensitive_name(part) for part in path.parts):
        raise ValueError("access to sensitive paths is denied")
    return path


def validate_test_command(command: tuple[str, ...]) -> None:
    if command not in ALLOWED_TEST_COMMANDS:
        raise ValueError(f"test command is not allowlisted: {command!r}")
