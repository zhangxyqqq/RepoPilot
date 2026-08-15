import re
import shutil
import subprocess
from pathlib import Path

import pytest

from repopilot.evaluation.cases import BenchmarkCase, load_cases
from repopilot.sandbox import DockerSandbox, stage_repository


BENCHMARK_ROOT = Path("benchmarks/cases")
EXISTING_CASE_IDS = {
    "arithmetic_edge_case",
    "config_precedence",
    "retry_off_by_one",
    "string_normalization",
}
NEW_CASE_IDS = {
    "inherited_permission_union",
    "layered_feature_flags",
    "pagination_exact_multiple",
    "reservation_rollback",
    "settings_cache_invalidation",
    "shipping_threshold",
    "tax_exemption_routing",
    "validation_pipeline_order",
}
CASES = {case.case_id: case for case in load_cases(BENCHMARK_ROOT)}


def _patch_paths(patch: str) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/(.+)$", patch, flags=re.MULTILINE))


def _run_configured_tests(case: BenchmarkCase, workspace: Path) -> dict[str, object]:
    with DockerSandbox(
        workspace,
        test_command=case.test_command,
        command_timeout_seconds=30,
    ) as sandbox:
        result = sandbox.invoke("run_tests", {})
    assert result["ok"], result.get("error")
    return result["result"]


def test_benchmark_corpus_has_complete_and_localized_cases():
    assert set(CASES) == EXISTING_CASE_IDS | NEW_CASE_IDS
    assert len(CASES) == 12

    for case in CASES.values():
        assert case.repository.is_dir()
        assert case.hidden_tests.is_dir()
        assert (case.repository / "tests").is_dir()
        assert list((case.repository / "tests").glob("test_*.py"))
        assert list(case.hidden_tests.glob("test_*.py"))
        assert case.solution_patch.strip()
        assert _patch_paths(case.solution_patch) == set(case.expected_fix_files)
        assert tuple(case.expected_fix_files) in case.allowed_fix_sets
        for fix_set in case.allowed_fix_sets:
            for path in fix_set:
                assert (case.repository / path).is_file()
        for expected_path in case.expected_fix_files:
            assert Path(expected_path).name.lower() not in case.issue.lower()

    for case_id in NEW_CASE_IDS:
        case = CASES[case_id]
        production_modules = [
            path
            for path in case.repository.rglob("*.py")
            if "tests" not in path.parts and path.name != "__init__.py"
        ]
        assert len(production_modules) >= 3
        for path in case.expected_fix_files:
            assert Path(path).stem.lower() not in case.issue.lower()


@pytest.mark.docker
@pytest.mark.parametrize("case_id", sorted(NEW_CASE_IDS))
def test_new_case_reproduces_bug_in_public_and_hidden_tests(case_id: str, tmp_path: Path):
    case = CASES[case_id]
    public_workspace = stage_repository(case.repository, tmp_path / "public-buggy")
    assert _run_configured_tests(case, public_workspace)["passed"] is False

    hidden_workspace = stage_repository(case.repository, tmp_path / "hidden-buggy")
    shutil.rmtree(hidden_workspace / "tests")
    stage_repository(case.hidden_tests, hidden_workspace / "tests")
    assert _run_configured_tests(case, hidden_workspace)["passed"] is False


@pytest.mark.docker
@pytest.mark.parametrize("case_id", sorted(CASES))
def test_reference_patch_passes_public_and_hidden_tests(case_id: str, tmp_path: Path):
    """Validate the reference solution without using the agent or scripted model."""

    case = CASES[case_id]
    patched_workspace = stage_repository(case.repository, tmp_path / "patched")
    applied = subprocess.run(
        ["git", "apply", "--whitespace=error-all", str((Path.cwd() / case.root / "solution.patch").resolve())],
        cwd=patched_workspace,
        text=True,
        capture_output=True,
    )
    assert applied.returncode == 0, applied.stderr
    assert _run_configured_tests(case, patched_workspace)["passed"] is True

    hidden_workspace = stage_repository(patched_workspace, tmp_path / "hidden")
    shutil.rmtree(hidden_workspace / "tests")
    stage_repository(case.hidden_tests, hidden_workspace / "tests")
    assert _run_configured_tests(case, hidden_workspace)["passed"] is True
