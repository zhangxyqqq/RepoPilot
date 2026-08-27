from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


_TEST_TARGET = re.compile(r"[A-Za-z0-9_.\-/]+\.py")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class TrustedTestPlan:
    """Controller-owned, data-only test command for one pinned benchmark instance."""

    plan_id: str
    version: int
    instance_id: str
    command: tuple[str, ...]
    source: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrustedTestPlan":
        allowed = {"plan_id", "version", "instance_id", "command", "source"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown trusted test-plan fields: {sorted(unknown)}")
        command = value.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValueError("trusted test-plan command must be a JSON array of strings")
        plan = cls(
            plan_id=str(value.get("plan_id", "")),
            version=int(value.get("version", 0)),
            instance_id=str(value.get("instance_id", "")),
            command=tuple(command),
            source=str(value.get("source", "")),
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        if not self.plan_id or self.version != 1 or not self.instance_id or not self.source:
            raise ValueError("trusted test plan requires a non-empty ID, instance, source, and version 1")
        if len(self.command) != 5 or self.command[:4] != ("python", "-m", "pytest", "-q"):
            raise ValueError("trusted test plan must be exactly: python -m pytest -q <relative-test-file>")
        target = self.command[4]
        path = PurePosixPath(target)
        if not _TEST_TARGET.fullmatch(target) or path.is_absolute() or ".." in path.parts:
            raise ValueError("trusted pytest target must be one relative Python test file")
        basename = path.name
        test_like_name = basename.startswith("test_") or basename.endswith("_test.py")
        within_test_tree = any(part in {"tests", "testing"} for part in path.parts[:-1])
        root_test_file = len(path.parts) == 1 and test_like_name
        if basename in {"conftest.py", "__init__.py"} or not test_like_name:
            raise ValueError("trusted pytest target must be a test-named Python file")
        if not (within_test_tree or root_test_file):
            raise ValueError("trusted pytest target must remain in a test tree or be a root test file")

    @property
    def content_hash(self) -> str:
        payload = {
            "plan_id": self.plan_id,
            "version": self.version,
            "instance_id": self.instance_id,
            "command": list(self.command),
            "source": self.source,
        }
        return "sha256:" + hashlib.sha256(_canonical_json(payload).encode()).hexdigest()

    def sandbox_command(self, interpreter: str) -> tuple[str, ...]:
        if not interpreter.startswith("/"):
            raise ValueError("trusted sandbox interpreter must be an absolute path")
        return (interpreter, *self.command[1:])
