from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunLimits:
    max_iterations: int = 30
    max_repair_cycles: int = 3
    command_timeout_seconds: int = 30
    total_timeout_seconds: int = 300
    max_observation_chars: int = 20_000
    max_patch_chars: int = 50_000


@dataclass(frozen=True)
class SandboxConfig:
    image: str = "repopilot-sandbox:0.1.0"
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 128


@dataclass(frozen=True)
class RunConfig:
    repository: Path
    issue: str
    output_dir: Path
    test_command: tuple[str, ...] = ("python", "-m", "pytest", "-q")
    limits: RunLimits = RunLimits()
    sandbox: SandboxConfig = SandboxConfig()
