from __future__ import annotations

from pathlib import Path

import pytest

from repopilot.sandbox import DockerSandbox


@pytest.mark.docker
def test_hybrid_retrieval_runs_inside_hardened_sandbox_without_worktree_cache(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def rollback_reservations(items):\n    return items\n",
        encoding="utf-8",
    )
    (repository / "distractor.py").write_text("def allocation_report():\n    return []\n", encoding="utf-8")
    with DockerSandbox(
        repository,
        test_command=("python", "-m", "pytest", "-q"),
        command_timeout_seconds=30,
        issue="undo an allocation",
        retrieval={"strategy": "hybrid", "max_files": 2, "max_chars": 2000},
    ) as sandbox:
        response = sandbox.invoke("list_files", {})
        inspection = sandbox.invoke("git_diff", {})

    assert response["ok"] is True
    retrieval = response["result"]["repository_context"]["retrieval"]
    assert retrieval["strategy"] == "hybrid"
    assert retrieval["fallback"] is None
    assert retrieval["cache"]["location"] == "ephemeral_tmpfs"
    assert retrieval["cache"]["persistent_service"] is False
    assert inspection["ok"] is True
    assert inspection["result"]["changed_files"] == []
    assert not any("retrieval-cache" in str(path) for path in repository.rglob("*"))
