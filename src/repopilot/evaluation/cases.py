from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repopilot.llm.scripted import ScriptedModel
from repopilot.models import AgentAction


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    root: Path
    issue: str
    search_query: str
    expected_fix_files: tuple[str, ...]
    allowed_fix_sets: tuple[tuple[str, ...], ...]
    test_command: tuple[str, ...]

    @property
    def repository(self) -> Path:
        return self.root / "repo"

    @property
    def hidden_tests(self) -> Path:
        return self.root / "hidden_tests"

    @property
    def solution_patch(self) -> str:
        return (self.root / "solution.patch").read_text(encoding="utf-8")

    def scripted_model(self) -> ScriptedModel:
        target = self.expected_fix_files[0]
        return ScriptedModel(
            [
                AgentAction("tool", "list_files", {}),
                AgentAction("tool", "search_code", {"query": self.search_query}),
                AgentAction("tool", "read_file", {"path": target, "start_line": 1, "end_line": 200}),
                AgentAction("plan", content=f"PLAN: update {target} with the minimal behavior fix, then run tests."),
                AgentAction("tool", "apply_patch", {"patch": self.solution_patch}),
                AgentAction("tool", "run_tests", {}),
                AgentAction("tool", "git_diff", {}),
                AgentAction("final", content="FINAL: implemented the focused fix and verified the public tests."),
            ],
            name=f"scripted-{self.case_id}",
        )


def load_cases(root: Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for task_path in sorted(root.glob("*/task.json")):
        data: dict[str, Any] = json.loads(task_path.read_text(encoding="utf-8"))
        expected = tuple(data["expected_fix_files"])
        allowed = tuple(tuple(paths) for paths in data.get("allowed_fix_sets", [expected]))
        cases.append(
            BenchmarkCase(
                case_id=data["id"],
                root=task_path.parent,
                issue=data["issue"],
                search_query=data["search_query"],
                expected_fix_files=expected,
                allowed_fix_sets=allowed,
                test_command=tuple(data.get("test_command", ["python", "-m", "pytest", "-q"])),
            )
        )
    if not cases:
        raise ValueError(f"no benchmark cases found below {root}")
    return cases
