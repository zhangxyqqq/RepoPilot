from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from repopilot.config import SandboxConfig
from repopilot.sandbox.docker import SandboxError
from repopilot.sandbox.test_plan import TrustedTestPlan


RUNNER_INTERPRETER = "/opt/miniconda3/bin/python"
TEST_INTERPRETER = "/opt/miniconda3/envs/testbed/bin/python"
_DIGEST_IMAGE = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")
_CREDENTIAL_NAME = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential|ssh_auth_sock|aws_|openai|deepseek)",
    re.IGNORECASE,
)


class SWEbenchSandbox:
    """Security-gated feasibility adapter; it does not run a model or grade predictions."""

    def __init__(
        self,
        workspace: Path,
        *,
        instance_id: str,
        image: str,
        expected_base_commit: str,
        test_plan: TrustedTestPlan,
        issue: str = "",
        command_timeout_seconds: int = 120,
        config: SandboxConfig = SandboxConfig(),
        forbidden_host_roots: Iterable[Path] = (),
    ):
        self.workspace = workspace.resolve(strict=True)
        self.instance_id = instance_id
        self.image = image
        self.expected_base_commit = expected_base_commit
        self.test_plan = test_plan
        self.issue = issue
        self.command_timeout_seconds = min(max(command_timeout_seconds, 1), 120)
        self.config = config
        self.forbidden_host_roots = tuple(path.resolve(strict=True) for path in forbidden_host_roots)
        self.container_name = f"repopilot-sweb-{uuid.uuid4().hex[:12]}"
        self._started = False
        self._bundle: tempfile.TemporaryDirectory[str] | None = None
        if test_plan.instance_id != instance_id:
            raise ValueError("trusted test plan does not match the SWE-bench instance")
        if not _DIGEST_IMAGE.fullmatch(image):
            raise ValueError("SWE-bench feasibility images must be pinned by sha256 digest")
        if not re.fullmatch(r"[0-9a-f]{40}", expected_base_commit):
            raise ValueError("expected SWE-bench base commit must be a full Git commit")

    @staticmethod
    def project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def _stage_runner_bundle(self) -> Path:
        self._bundle = tempfile.TemporaryDirectory(prefix="repopilot-sweb-runner-")
        root = Path(self._bundle.name)
        project = self.project_root()
        shutil.copy2(project / "src/repopilot/sandbox/sandbox_runner.py", root / "sandbox_runner.py")
        shutil.copy2(project / "src/repopilot/sandbox/repository_context.py", root / "repository_context.py")
        package = root / "repopilot"
        package.mkdir()
        shutil.copy2(project / "src/repopilot/__init__.py", package / "__init__.py")
        shutil.copytree(project / "src/repopilot/retrieval", package / "retrieval")
        shutil.copytree(project / "configs/retrieval", package / "configs/retrieval")
        return root.resolve(strict=True)

    def start(self) -> None:
        if self._started:
            return
        revision = subprocess.run(
            ["git", "-C", str(self.workspace), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
        )
        if revision.returncode != 0 or revision.stdout.strip() != self.expected_base_commit:
            raise SandboxError("staged SWE-bench worktree is not at the frozen base commit")
        image = subprocess.run(["docker", "image", "inspect", self.image], text=True, capture_output=True)
        if image.returncode != 0:
            raise SandboxError("pinned official SWE-bench image is not acquired locally")
        bundle = self._stage_runner_bundle()
        for forbidden in self.forbidden_host_roots:
            if self.workspace == forbidden or forbidden in self.workspace.parents:
                raise SandboxError("agent workspace resolves inside a forbidden host root")
            if bundle == forbidden or forbidden in bundle.parents:
                raise SandboxError("runner bundle resolves inside a forbidden host root")
        command = [
            "docker", "run", "-d", "--rm",
            "--platform", "linux/amd64",
            "--name", self.container_name,
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,uid=10001,gid=10001,mode=1777",
            "--tmpfs", "/home/repopilot:rw,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=700",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", self.config.memory,
            "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit),
            "--user", "10001:10001",
            "--workdir", "/testbed",
            "--env", "HOME=/home/repopilot",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "REPOPILOT_WORKSPACE=/testbed",
            "--env", "REPOPILOT_TRUSTED_TEST_COMMAND=" + json.dumps(
                list(self.test_plan.sandbox_command(TEST_INTERPRETER)), separators=(",", ":")
            ),
            "--mount", f"type=bind,src={self.workspace},dst=/testbed",
            "--mount", f"type=bind,src={bundle},dst=/opt/repopilot,readonly",
            self.image,
            "sleep", "infinity",
        ]
        started = subprocess.run(command, text=True, capture_output=True)
        if started.returncode != 0:
            self.close()
            raise SandboxError(f"SWE-bench sandbox start failed: {started.stderr.strip()}")
        self._started = True

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
            payload["command"] = list(self.test_plan.sandbox_command(TEST_INTERPRETER))
            payload["timeout_seconds"] = self.command_timeout_seconds
        elif tool_name == "list_files":
            payload["_issue"] = self.issue
            payload["_retrieval"] = {"strategy": "structural"}
        command = [
            "docker", "exec", self.container_name,
            RUNNER_INTERPRETER, "/opt/repopilot/sandbox_runner.py",
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
            return {
                "ok": False,
                "error": f"Docker command timed out: {exc}",
                "latency_ms": (perf_counter() - started) * 1000,
            }
        latency_ms = (perf_counter() - started) * 1000
        if completed.returncode != 0:
            return {"ok": False, "error": completed.stderr.strip() or "Docker exec failed", "latency_ms": latency_ms}
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": "sandbox returned invalid JSON", "latency_ms": latency_ms}
        result["latency_ms"] = latency_ms
        return result

    def inspect_security(self) -> dict[str, Any]:
        if not self._started:
            raise SandboxError("sandbox is not running")
        inspected = json.loads(
            subprocess.run(
                ["docker", "inspect", self.container_name],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        )[0]
        host = inspected["HostConfig"]
        mounts = inspected["Mounts"]
        environment = inspected["Config"]["Env"]
        environment_names = sorted(item.split("=", 1)[0] for item in environment)
        identity = subprocess.run(
            ["docker", "exec", self.container_name, "id", "-u"], text=True, capture_output=True
        )
        revision = subprocess.run(
            ["docker", "exec", self.container_name, "git", "rev-parse", "HEAD"], text=True, capture_output=True
        )
        docker_socket = subprocess.run(
            [
                "docker", "exec", self.container_name, RUNNER_INTERPRETER, "-c",
                "import os,sys;sys.exit(0 if os.path.exists('/var/run/docker.sock') else 1)",
            ],
            text=True,
            capture_output=True,
        )
        root_write = subprocess.run(
            [
                "docker", "exec", self.container_name, RUNNER_INTERPRETER, "-c",
                "open('/repopilot-root-write-probe','w').write('x')",
            ],
            text=True,
            capture_output=True,
        )
        rw_mounts = sorted(mount["Destination"] for mount in mounts if mount.get("RW"))
        ro_mounts = sorted(mount["Destination"] for mount in mounts if not mount.get("RW"))
        mount_sources = [Path(mount["Source"]).resolve(strict=False) for mount in mounts if mount.get("Source")]
        host_home = Path.home().resolve(strict=True)

        def within(path: Path, root: Path) -> bool:
            return path == root or root in path.parents

        assertions = {
            "non_root_user": identity.returncode == 0 and identity.stdout.strip() == "10001",
            "network_disabled": host["NetworkMode"] == "none",
            "root_filesystem_read_only": host["ReadonlyRootfs"] is True and root_write.returncode != 0,
            "capabilities_dropped": host["CapDrop"] == ["ALL"],
            "no_new_privileges": "no-new-privileges" in (host["SecurityOpt"] or []),
            "memory_bounded": int(host["Memory"]) > 0,
            "cpu_bounded": int(host["NanoCpus"]) > 0,
            "pids_bounded": int(host["PidsLimit"]) == self.config.pids_limit,
            "only_staged_workspace_writable": rw_mounts == ["/testbed"],
            "runner_bundle_read_only": ro_mounts == ["/opt/repopilot"],
            "docker_socket_absent": docker_socket.returncode != 0 and "/var/run/docker.sock" not in rw_mounts + ro_mounts,
            "host_home_not_mounted": not any(within(source, host_home) for source in mount_sources),
            "forbidden_host_roots_not_mounted": not any(
                within(source, forbidden) for source in mount_sources for forbidden in self.forbidden_host_roots
            ),
            "ssh_agent_not_mounted": not any("ssh" in destination.casefold() for destination in rw_mounts + ro_mounts),
            "credentials_absent": not any(_CREDENTIAL_NAME.search(name) for name in environment_names),
            "staged_revision_matches": revision.returncode == 0 and revision.stdout.strip() == self.expected_base_commit,
        }
        return {
            "passed": all(assertions.values()),
            "assertions": assertions,
            "evidence": {
                "container_user": inspected["Config"]["User"],
                "network_mode": host["NetworkMode"],
                "read_only_rootfs": host["ReadonlyRootfs"],
                "cap_drop": host["CapDrop"],
                "security_opt": host["SecurityOpt"],
                "memory_bytes": host["Memory"],
                "nano_cpus": host["NanoCpus"],
                "pids_limit": host["PidsLimit"],
                "read_write_mount_destinations": rw_mounts,
                "read_only_mount_destinations": ro_mounts,
                "environment_variable_names": environment_names,
                "staged_revision": revision.stdout.strip(),
            },
        }

    def close(self) -> None:
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.container_name], text=True, capture_output=True)
            self._started = False
        if self._bundle is not None:
            self._bundle.cleanup()
            self._bundle = None

    def __enter__(self) -> "SWEbenchSandbox":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def scan_contamination(
    workspace: Path,
    *,
    visible_artifacts: Iterable[object] = (),
    forbidden_names: Iterable[str] = (),
    forbidden_blobs: Iterable[str] = (),
) -> dict[str, Any]:
    """Conservative text/name scan over the exact agent-visible boundary."""

    forbidden_name_set = {name.casefold() for name in forbidden_names}
    name_hits: list[str] = []
    file_texts: list[str] = []
    for path in workspace.rglob("*"):
        relative = path.relative_to(workspace)
        if ".git" in relative.parts:
            continue
        if path.name.casefold() in forbidden_name_set:
            name_hits.append(str(relative))
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 1_000_000:
            try:
                file_texts.append(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                pass
    visible = "\n".join(file_texts) + "\n" + "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in visible_artifacts
    )
    blob_hits = [
        "sha256:" + __import__("hashlib").sha256(blob.encode()).hexdigest()
        for blob in forbidden_blobs
        if blob and blob.rstrip() in visible
    ]
    return {
        "passed": not name_hits and not blob_hits,
        "forbidden_name_hits": sorted(name_hits),
        "forbidden_blob_hash_hits": sorted(blob_hits),
    }
