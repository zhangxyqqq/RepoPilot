from pathlib import Path

from repopilot.cli import build_parser
from repopilot.evaluation.swebench_feasibility import load_feasibility_profile
from repopilot.retrieval import RetrievalConfig
from repopilot.sandbox import sandbox_runner
from repopilot.tools.definitions import PUBLIC_TOOL_DEFINITIONS


def test_feasibility_spike_does_not_add_behavioral_cli_or_broaden_normal_tools() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "real-eval" not in help_text
    assert "real-grade" not in help_text
    assert [definition.name for definition in PUBLIC_TOOL_DEFINITIONS] == [
        "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff",
    ]
    assert sandbox_runner.ALLOWED_TEST_COMMANDS == {("python", "-m", "pytest", "-q")}


def test_p1_structural_default_and_real_validate_track_remain_unchanged() -> None:
    profile = load_feasibility_profile(Path("configs/evaluation/swebench_verified_feasibility.json"))
    assert profile["claim_boundary"].endswith("It does not authorize or report a live agent SWE-bench solve.")
    assert RetrievalConfig().strategy == "structural"
    real_world = Path("src/repopilot/evaluation/real_world.py").read_text(encoding="utf-8")
    assert '"behavioral_tests_run": False' in real_world
