"""Adapter to the authoritative in-process agent entry point, not the CLI."""
from dataclasses import asdict
from repopilot.agent import run_agent
from repopilot.service.artifacts import LocalArtifacts
from repopilot.config import RunConfig, SandboxConfig
from repopilot.llm import ProviderConfig, create_model
from repopilot.llm.scripted import ScriptedModel
from repopilot.models import AgentAction
from repopilot.trajectory import redact_value
from repopilot.trajectory.schema import secret_redaction
from repopilot.sandbox.process import run_process


def sandbox_name(task_id):
    return f"repopilot-service-{task_id}"


class AgentExecutor:
    def __init__(self, settings, artifacts=None):
        self.settings = settings
        self.artifacts = artifacts if artifacts is not None else LocalArtifacts(settings.artifact_root)

    def cleanup(self, claim):
        # Called while holding the execution lock, before every attempt and after
        # execution. Do not proceed if Docker cannot confirm absence of an orphan.
        name = sandbox_name(claim.task["id"])
        found = run_process(["docker", "ps", "-aq", "--no-trunc", "--filter", f"name=^/{name}$"],
                               check=True, capture_output=True, text=True, timeout=30)
        if found.stdout.strip():
            run_process(["docker", "rm", "-f", *found.stdout.split()], check=True, capture_output=True, timeout=30)

    def __call__(self, claim):
        with secret_redaction(self.settings.secrets):
            return self.execute(claim)

    def execute(self, claim):
        task = claim.task
        if self.settings.scripted and task["provider"] != "scripted":
            raise ValueError("scripted workers cannot execute provider-backed tasks")
        repository = self.settings.repository(task["payload"]["repository"])
        if task["provider"] == "scripted":
            if not self.settings.scripted:
                raise ValueError("scripted tasks require explicit operator opt-in")
            model = ScriptedModel([AgentAction("final", content="Deterministic service smoke: collect tests and diff.")],
                                  name="scripted-final")
        else:
            model = create_model(ProviderConfig(provider=task["provider"], model=task["model"]))
        result, _ = run_agent(
            RunConfig(repository=repository, issue=task["payload"]["issue"], output_dir=self.settings.artifact_root,
                      sandbox=SandboxConfig(container_name=sandbox_name(task["id"]), build_image=False)),
            model, run_id=str(claim.run["id"]),
        )
        value = asdict(result)
        value.update(self.artifacts.metadata(str(claim.run['id'])))
        return redact_value(value)
