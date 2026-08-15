import json
import subprocess
from pathlib import Path

import pytest

from repopilot.sandbox import DockerSandbox, stage_repository


@pytest.mark.docker
def test_sandbox_enforces_paths_and_runs_only_fixed_tests(tmp_path: Path):
    source = Path("benchmarks/cases/arithmetic_edge_case/repo").resolve()
    workspace = stage_repository(source, tmp_path / "workspace")

    with DockerSandbox(
        workspace,
        test_command=("python", "-m", "pytest", "-q"),
        command_timeout_seconds=30,
        issue="safe_divide fails for negative denominators",
    ) as sandbox:
        inspected = json.loads(
            subprocess.run(
                ["docker", "inspect", sandbox.container_name],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        )[0]
        listing = sandbox.invoke("list_files", {})
        escaped = sandbox.invoke("read_file", {"path": "../../etc/passwd"})
        patched = sandbox.invoke(
            "apply_patch",
            {
                "patch": """*** Begin Patch
*** Update File: calculator.py
@@
-    if denominator <= 0:
+    if denominator == 0:
*** Update File: tests/test_calculator.py
@@
 def test_positive_division():
     assert safe_divide(12, 3) == 4
+    assert safe_divide(12, -3) == -4
*** End Patch"""
            },
        )
        tests = sandbox.invoke("run_tests", {})
        diff = sandbox.invoke("git_diff", {})

    assert listing["ok"]
    assert "calculator.py" in listing["result"]["files"]
    repository_context = listing["result"]["repository_context"]
    assert repository_context["format"] == "python_ast_outline_v2"
    assert "safe_divide" in repository_context["ranking"]["issue_terms"]
    assert "calculator.py [module=calculator, role=source]" in repository_context["map"]
    assert "def safe_divide(numerator: float, denominator: float) -> float" in repository_context["map"]
    assert "tests/test_calculator.py [module=tests.test_calculator, role=test]" in repository_context["map"]
    assert not escaped["ok"]
    assert patched["ok"]
    assert patched["result"]["ignored_paths"] == ["tests/test_calculator.py"]
    assert tests["ok"] and tests["result"]["passed"]
    assert diff["result"]["changed_files"] == ["calculator.py"]
    assert not (workspace / ".env").exists()
    host_config = inspected["HostConfig"]
    assert host_config["NetworkMode"] == "none"
    assert host_config["ReadonlyRootfs"] is True
    assert host_config["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in host_config["SecurityOpt"]
    assert host_config["Memory"] > 0
    assert host_config["NanoCpus"] > 0
    assert host_config["PidsLimit"] == 128
    assert not any(value.startswith("OPENAI_API_KEY=") for value in inspected["Config"]["Env"])
    assert not any(value.startswith("DEEPSEEK_API_KEY=") for value in inspected["Config"]["Env"])
