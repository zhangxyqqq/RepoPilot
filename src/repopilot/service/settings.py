from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    workspace_root: Path
    artifact_root: Path
    api_token: str = field(repr=False)
    provider: str = "openai"
    model: str = "gpt-4.1-mini"
    lease_seconds: int = 30
    max_attempts: int = 3
    poll_seconds: float = 1
    hard_timeout_seconds: int = 900
    scripted: bool = False
    max_inflight: int = 4096

    def __post_init__(self):
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("a PostgreSQL connection URL is required")
        if len(self.api_token) < 16 or self.lease_seconds < 3 or self.max_attempts < 1:
            raise ValueError("token >= 16 characters, lease >= 3 seconds, and positive attempts required")
        if self.max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        if self.poll_seconds <= 0 or self.hard_timeout_seconds < self.lease_seconds:
            raise ValueError("invalid worker timing configuration")
        if self.provider not in {"openai", "deepseek"}:
            raise ValueError("service supports operator-configured openai or deepseek providers")
        if not self.scripted:
            from repopilot.llm import ProviderConfig
            ProviderConfig(provider=self.provider, model=self.model).validate()
        root = self.workspace_root.resolve()
        artifacts = self.artifact_root.resolve()
        if root == artifacts or root in artifacts.parents or artifacts in root.parents:
            raise ValueError("workspace and artifact roots must be separate")

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ["REPOPILOT_DATABASE_URL"],
            workspace_root=Path(os.environ["REPOPILOT_WORKSPACE_ROOT"]).resolve(),
            artifact_root=Path(os.environ["REPOPILOT_ARTIFACT_ROOT"]).resolve(),
            api_token=os.environ["REPOPILOT_API_TOKEN"],
            provider=os.getenv("REPOPILOT_PROVIDER", "openai"),
            model=os.getenv("REPOPILOT_MODEL", "gpt-4.1-mini"),
            lease_seconds=int(os.getenv("REPOPILOT_LEASE_SECONDS", "30")),
            max_attempts=int(os.getenv("REPOPILOT_MAX_ATTEMPTS", "3")),
            hard_timeout_seconds=int(os.getenv("REPOPILOT_HARD_TIMEOUT_SECONDS", "900")),
            max_inflight=int(os.getenv("REPOPILOT_MAX_INFLIGHT", "4096")),
            scripted=os.getenv("REPOPILOT_SCRIPTED", "0") == "1",
        )

    @property
    def secrets(self):
        return tuple(value for value in (
            self.api_token,
            os.getenv("OPENAI_API_KEY"), os.getenv("DEEPSEEK_API_KEY"),
        ) if value)

    def repository(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("repository must be a relative path under the approved root")
        root = self.workspace_root.resolve(strict=True)
        result = (root / path).resolve(strict=True)
        if not result.is_relative_to(root) or not result.is_dir():
            raise ValueError("repository is outside the approved root or is not a directory")
        return result
