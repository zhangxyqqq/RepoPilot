from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from repopilot.config import RunConfig
from repopilot.sandbox import DockerSandbox, stage_repository
from repopilot.tools import ToolRegistry
from repopilot.trajectory import TrajectoryRecorder


@dataclass
class RunSession:
    """One staged workspace, sandbox, registry, and trace lifecycle."""

    config: RunConfig
    run_id: str
    run_directory: Path
    workspace: Path
    recorder: TrajectoryRecorder
    sandbox: DockerSandbox
    tools: ToolRegistry
    _closed: bool = False

    @classmethod
    def start(
        cls,
        config: RunConfig,
        *,
        run_id: str,
        model_metadata: dict[str, Any],
        expose_source_path: bool = True,
        transport: str = "direct",
    ) -> "RunSession":
        run_directory = config.output_dir.resolve() / run_id
        if run_directory.exists():
            raise FileExistsError(f"run output already exists: {run_directory}")
        workspace = stage_repository(config.repository, run_directory / "workspace")
        recorder = TrajectoryRecorder(
            run_directory / "trajectory.jsonl",
            run_id=run_id,
            metadata={
                "issue": config.issue,
                "repository": str(config.repository.resolve()) if expose_source_path else "staged-workspace",
                "test_command": list(config.test_command),
                "model": model_metadata,
                "limits": asdict(config.limits),
                "sandbox": asdict(config.sandbox),
                "evaluation_profile": config.evaluation_profile,
                "tool_transport": transport,
            },
        )
        sandbox = DockerSandbox(
            workspace,
            test_command=config.test_command,
            command_timeout_seconds=config.limits.command_timeout_seconds,
            issue=config.issue,
            config=config.sandbox,
            retrieval=config.retrieval,
        )
        try:
            sandbox.start()
        except Exception:
            sandbox.close()
            raise
        return cls(
            config=config,
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
            recorder=recorder,
            sandbox=sandbox,
            tools=ToolRegistry(sandbox),
        )

    @property
    def trajectory_path(self) -> Path:
        return self.recorder.path

    def close(self) -> None:
        if not self._closed:
            self.sandbox.close()
            self._closed = True

    def finish_transport(self) -> None:
        self.recorder.record(
            "run_finished",
            success=True,
            stop_reason="transport_closed",
            iterations=0,
            repair_cycles=0,
            tool_calls=None,
            unnecessary_tool_calls=self.tools.unnecessary_calls,
            token_usage={},
            changed_files=[],
        )

    def __enter__(self) -> "RunSession":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
