from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any

from repopilot.config import SandboxConfig
from repopilot.sandbox.policy import validate_test_command


class SandboxError(RuntimeError):
    pass


class DockerSandbox:
    _built_images: set[str] = set()

    def __init__(
        self,
        workspace: Path,
        *,
        test_command: tuple[str, ...],
        command_timeout_seconds: int,
        config: SandboxConfig = SandboxConfig(),
    ):
        self.workspace = workspace.resolve(strict=True)
        self.test_command = test_command
        self.command_timeout_seconds = command_timeout_seconds
        self.config = config
        self.container_name = f"repopilot-{uuid.uuid4().hex[:12]}"
        self._started = False
        validate_test_command(test_command)

    @staticmethod
    def project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def ensure_image(self) -> None:
        if self.config.image in self._built_images:
            return
        built = subprocess.run(
            ["docker", "build", "--pull", "-t", self.config.image, "."],
            cwd=self.project_root(),
            text=True,
            capture_output=True,
        )
        if built.returncode != 0:
            raise SandboxError(f"sandbox image build failed:\n{built.stdout}\n{built.stderr}")
        self._built_images.add(self.config.image)

    def start(self) -> None:
        if self._started:
            return
        self.ensure_image()
        command = [
            "docker", "run", "-d", "--rm",
            "--name", self.container_name,
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--tmpfs", "/home/repopilot:rw,nosuid,nodev,size=16m",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", self.config.memory,
            "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit),
            "--user", "10001:10001",
            "--env", "HOME=/home/repopilot",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace",
            self.config.image,
        ]
        started = subprocess.run(command, text=True, capture_output=True)
        if started.returncode != 0:
            raise SandboxError(f"sandbox start failed: {started.stderr.strip()}")
        self._started = True
        if not (self.workspace / ".git").exists():
            initialized = self.invoke("_init_repo", {}, timeout_seconds=30)
            if not initialized["ok"]:
                self.close()
                raise SandboxError(initialized["error"])

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not self._started:
            raise SandboxError("sandbox is not running")
        payload = dict(arguments)
        if tool_name == "run_tests":
            payload["command"] = list(self.test_command)
            payload["timeout_seconds"] = self.command_timeout_seconds
        command = [
            "docker", "exec", self.container_name,
            "python", "/opt/repopilot/sandbox_runner.py",
            tool_name, json.dumps(payload, ensure_ascii=False),
        ]
        started = perf_counter()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_seconds or self.command_timeout_seconds + 10,
            )
        except subprocess.TimeoutExpired as exc:
            return {"ok": False, "error": f"Docker command timed out: {exc}", "latency_ms": (perf_counter() - started) * 1000}
        latency_ms = (perf_counter() - started) * 1000
        if completed.returncode != 0:
            return {"ok": False, "error": completed.stderr.strip() or "Docker exec failed", "latency_ms": latency_ms}
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": "sandbox returned invalid JSON", "latency_ms": latency_ms}
        result["latency_ms"] = latency_ms
        return result

    def close(self) -> None:
        if self._started:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                text=True,
                capture_output=True,
            )
            self._started = False

    def __enter__(self) -> "DockerSandbox":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
