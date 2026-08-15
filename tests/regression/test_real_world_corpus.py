import json
from pathlib import Path

from repopilot.evaluation.real_world import load_real_world_tasks, patch_paths, validate_task_metadata


REAL_WORLD_ROOT = Path("benchmarks/real_world")


def test_real_world_inventory_is_separate_and_complete():
    inventory = json.loads((REAL_WORLD_ROOT / "inventory.json").read_text(encoding="utf-8"))
    tasks = load_real_world_tasks(REAL_WORLD_ROOT)

    assert inventory["task_count"] == 5
    assert len(tasks) == 5
    assert {task.task_id for task in tasks} == {item["id"] for item in inventory["tasks"]}
    assert {task.upstream_repository for task in tasks} == {
        "pallets/flask",
        "psf/requests",
        "pytest-dev/pytest",
    }


def test_real_world_metadata_and_upstream_patches_are_internally_consistent():
    for task in load_real_world_tasks(REAL_WORLD_ROOT):
        validate_task_metadata(task)
        assert task.metadata["source"] == "SWE-bench Verified"
        assert task.metadata["dataset"] == "princeton-nlp/SWE-bench_Verified"
        assert task.environment["behavioral_validator"] == "official SWE-bench Docker harness"
        assert task.environment["agent_execution_network_required"] is False
        assert patch_paths(task.reference_patch) == task.expected_fix_files
        assert patch_paths(task.test_patch) == task.verification_test_files
        assert not set(task.expected_fix_files) & set(task.verification_test_files)
        assert all("test" not in Path(path).name.lower() for path in task.expected_fix_files)
