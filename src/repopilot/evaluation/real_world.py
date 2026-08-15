from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def patch_paths(patch: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(2) for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", patch, re.MULTILINE)))


@dataclass(frozen=True)
class RealWorldTask:
    task_id: str
    root: Path
    upstream_repository: str
    upstream_url: str
    base_commit: str
    issue_description: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    expected_fix_files: tuple[str, ...]
    verification_test_files: tuple[str, ...]
    environment: dict[str, Any]
    metadata: dict[str, Any]

    @property
    def reference_patch(self) -> str:
        return (self.root / self.metadata["reference_patch"]).read_text(encoding="utf-8")

    @property
    def test_patch(self) -> str:
        return (self.root / self.metadata["upstream_test_patch"]).read_text(encoding="utf-8")


def load_real_world_tasks(root: Path) -> list[RealWorldTask]:
    tasks: list[RealWorldTask] = []
    for task_path in sorted(root.glob("tasks/*/task.json")):
        data = json.loads(task_path.read_text(encoding="utf-8"))
        tasks.append(
            RealWorldTask(
                task_id=data["id"],
                root=task_path.parent,
                upstream_repository=data["upstream_repository"],
                upstream_url=data["upstream_url"],
                base_commit=data["base_commit"],
                issue_description=data["issue_description"],
                fail_to_pass=tuple(data["fail_to_pass"]),
                pass_to_pass=tuple(data["pass_to_pass"]),
                expected_fix_files=tuple(data["expected_fix_files"]),
                verification_test_files=tuple(data["verification_test_files"]),
                environment=data["environment"],
                metadata=data,
            )
        )
    if not tasks:
        raise ValueError(f"no real-world tasks found below {root}")
    return tasks


def validate_task_metadata(task: RealWorldTask) -> None:
    if not REPOSITORY_PATTERN.fullmatch(task.upstream_repository):
        raise ValueError(f"invalid upstream repository for {task.task_id}")
    expected_url = f"https://github.com/{task.upstream_repository}"
    if task.upstream_url != expected_url:
        raise ValueError(f"unexpected upstream URL for {task.task_id}")
    if not COMMIT_PATTERN.fullmatch(task.base_commit):
        raise ValueError(f"invalid base commit for {task.task_id}")
    if not task.issue_description.strip() or not task.fail_to_pass:
        raise ValueError(f"incomplete issue or verification metadata for {task.task_id}")
    if patch_paths(task.reference_patch) != task.expected_fix_files:
        raise ValueError(f"reference patch paths disagree for {task.task_id}")
    if patch_paths(task.test_patch) != task.verification_test_files:
        raise ValueError(f"test patch paths disagree for {task.task_id}")


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)


def _validate_checkout(task: RealWorldTask, checkout: Path) -> dict[str, Any]:
    checkout.mkdir(parents=True)
    commands = [
        ["git", "init", "-q", str(checkout)],
        ["git", "-C", str(checkout), "remote", "add", "origin", task.upstream_url],
        ["git", "-C", str(checkout), "fetch", "-q", "--depth", "1", "origin", task.base_commit],
        ["git", "-C", str(checkout), "checkout", "-q", "--detach", "FETCH_HEAD"],
    ]
    for command in commands:
        completed = _run(command)
        if completed.returncode != 0:
            return {"passed": False, "stage": "checkout", "error": completed.stderr.strip()}
    revision = _run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
    if revision != task.base_commit:
        return {"passed": False, "stage": "revision", "error": f"resolved {revision}"}

    reference_check = _run(["git", "apply", "--check", str((task.root / task.metadata["reference_patch"]).resolve())], cwd=checkout)
    if reference_check.returncode != 0:
        return {"passed": False, "stage": "reference_patch", "error": reference_check.stderr.strip()}
    reference_apply = _run(["git", "apply", str((task.root / task.metadata["reference_patch"]).resolve())], cwd=checkout)
    if reference_apply.returncode != 0:
        return {"passed": False, "stage": "reference_patch", "error": reference_apply.stderr.strip()}
    test_check = _run(["git", "apply", "--check", str((task.root / task.metadata["upstream_test_patch"]).resolve())], cwd=checkout)
    if test_check.returncode != 0:
        return {"passed": False, "stage": "test_patch", "error": test_check.stderr.strip()}

    compile_errors: list[str] = []
    for relative in task.expected_fix_files:
        if not relative.endswith(".py"):
            continue
        compiled = _run([sys.executable, "-m", "py_compile", str(checkout / relative)])
        if compiled.returncode != 0:
            compile_errors.append(f"{relative}: {compiled.stderr.strip()}")
    return {
        "passed": not compile_errors,
        "stage": "complete" if not compile_errors else "compile",
        "resolved_commit": revision,
        "reference_patch_applies": True,
        "test_patch_applies_after_reference": True,
        "changed_files": list(task.expected_fix_files),
        "compiled_python_files": [path for path in task.expected_fix_files if path.endswith(".py")],
        "compile_errors": compile_errors,
        "behavioral_tests_run": False,
        "behavioral_validator": task.environment["behavioral_validator"],
    }


def validate_real_world_references(task_root: Path, output_directory: Path) -> dict[str, Any]:
    tasks = load_real_world_tasks(task_root)
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="repopilot-real-world-") as temporary:
        temporary_root = Path(temporary)
        for task in tasks:
            validate_task_metadata(task)
            validation = _validate_checkout(task, temporary_root / task.task_id)
            results.append(
                {
                    "id": task.task_id,
                    "upstream_repository": task.upstream_repository,
                    "base_commit": task.base_commit,
                    "verification_tests": list(task.fail_to_pass),
                    **validation,
                }
            )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "track": "real_world_reference_integrity",
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "behavioral_tests_run": False,
        "cases": results,
        "aggregate": {
            "cases": len(results),
            "reference_integrity_passed": sum(bool(item["passed"]) for item in results),
            "live_agent_tasks_succeeded": None,
        },
    }
    gold_path = output_directory / "gold-predictions.jsonl"
    gold_path.write_text(
        "".join(
            json.dumps(
                {
                    "instance_id": task.task_id,
                    "model_name_or_path": "repopilot-reference-patch",
                    "model_patch": task.reference_patch,
                },
                sort_keys=True,
            )
            + "\n"
            for task in tasks
        ),
        encoding="utf-8",
    )
    report["official_harness"] = {
        "predictions_path": str(gold_path),
        "instance_ids": [task.task_id for task in tasks],
        "note": "Run with the official SWE-bench harness for behavioral gold-patch verification.",
    }
    json_path = output_directory / "reference-validation.json"
    markdown_path = output_directory / "reference-validation.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = [
        "# Real-world reference integrity",
        "",
        "> This is checkout/patch/compile validation, not behavioral test success or live-agent success.",
        "",
        "| Task | Repository | Revision | Integrity | Behavioral grading |",
        "|---|---|---|---:|---|",
    ]
    rows.extend(
        f"| `{item['id']}` | `{item['upstream_repository']}` | `{item['base_commit'][:12]}` | "
        f"{'pass' if item['passed'] else 'fail'} | not run |"
        for item in results
    )
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    report["report_paths"] = {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "gold_predictions": str(gold_path),
    }
    return report
