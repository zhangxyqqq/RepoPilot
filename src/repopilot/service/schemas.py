from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from repopilot.trajectory import redact_text


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    repository: str = Field(min_length=1, max_length=512)
    issue: str = Field(min_length=1, max_length=20000)
    # Provider, model, endpoints and secrets are operator-owned configuration.
    profile: Literal["default"] = "default"

    @field_validator("issue", "repository")
    @classmethod
    def no_credentials(cls, value):
        if "\x00" in value:
            raise ValueError("NUL is not valid PostgreSQL text")
        value.encode("utf-8")  # Reject lone surrogates before database serialization.
        if redact_text(value) != value:
            raise ValueError("credential-shaped text is not accepted")
        return value


class RunView(BaseModel):
    id: UUID
    task_id: UUID
    attempt: int
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "ABANDONED"]
    worker_id: str
    trace_id: str
    started_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    completed_at: datetime | None
    stop_reason: str | None
    error_code: str | None
    artifact_path: str | None


class TaskView(BaseModel):
    schema_version: Literal[1] = 1
    id: UUID
    request_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    payload: TaskCreate
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    latest_run_id: UUID | None
    runs: list[RunView] = Field(default_factory=list)


class ResultView(BaseModel):
    schema_version: Literal[1] = 1
    task_id: UUID
    run_id: UUID
    status: str
    stop_reason: str | None
    error_code: str | None
    result: dict[str, Any] | None


class TraceView(BaseModel):
    schema_version: Literal[1] = 1
    task_id: UUID
    runs: list[RunView]
    summaries: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ErrorView(BaseModel):
    detail: str
    code: str
    request_id: UUID
