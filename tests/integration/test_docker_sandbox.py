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
        tests = sandbox.invoke("run_tests", {})

    assert listing["ok"]
    assert "calculator.py" in listing["result"]["files"]
    assert not escaped["ok"]
    assert tests["ok"] and tests["result"]["passed"]
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
