from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from repopilot.agent.loop import AgentLoop
from repopilot.agent.recovery import RecoveryPolicy
from repopilot.config import SandboxConfig
from repopilot.evaluation.taxonomy import classify_failures, load_failure_taxonomy
from repopilot.llm.openai_adapter import OpenAICompatibleModel
from repopilot.llm.prompting import SYSTEM_PROMPT, build_transcript
from repopilot.sandbox.swebench import SWEbenchSandbox, scan_contamination
from repopilot.sandbox.test_plan import TrustedTestPlan
from repopilot.tools.definitions import PUBLIC_TOOL_DEFINITIONS, provider_tool_schemas
from repopilot.tools.registry import ToolRegistry
from repopilot.trajectory import TrajectoryRecorder, reconcile_summary, redact_value, summarize_trace
from repopilot.trajectory.reader import TraceReader


TRACK = "swebench_verified_behavioral_pilot"
INSTANCE_IDS = ("pallets__flask-5014", "pytest-dev__pytest-10051")
FORBIDDEN_NAMES = (
    "reference.patch",
    "test.patch",
    "gold.patch",
    "gold-predictions.jsonl",
    "gold_predictions.jsonl",
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode())


def _tool_catalog() -> dict[str, Any]:
    return {
        "catalog_version": 1,
        "definitions": [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
                "access": definition.access,
                "idempotency": definition.idempotency,
            }
            for definition in PUBLIC_TOOL_DEFINITIONS
        ],
        "provider_schemas": provider_tool_schemas(),
    }


def load_behavioral_profile(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version", "profile_id", "profile_version", "track", "frozen_at",
        "repetitions_per_instance", "random_seed", "instances", "official_environment",
        "provider", "agent", "controller", "limits", "retrieval", "sandbox",
        "evaluation", "protocol", "claim_boundary",
    }
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError(f"behavioral profile has missing or unknown fields: {sorted(set(value) ^ allowed)}")
    if (
        value["schema_version"] != 1
        or value["profile_version"] != 1
        or value["profile_id"] != "swebench-verified-behavioral-pilot"
        or value["track"] != TRACK
    ):
        raise ValueError("unsupported SWE-bench behavioral profile identity or version")
    if value["repetitions_per_instance"] != 1 or value["random_seed"] is not None:
        raise ValueError("behavioral pilot requires one repetition and no unsupported seed override")

    nested_fields = {
        "official_environment": {
            "dataset_id", "dataset_revision", "split", "harness_package", "harness_version",
            "harness_commit", "host_architecture", "image_architecture", "execution_mode",
            "harness_retry_limit",
        },
        "provider": {
            "provider", "endpoint_owner", "endpoint", "endpoint_api", "ollama_version", "model",
            "model_manifest_sha256", "model_blob_sha256", "model_parameter_size",
            "model_quantization", "sdk_timeout_seconds", "sdk_retry_limit",
            "credentials_required", "compatibility_probe",
        },
        "agent": {
            "system_prompt_source", "system_prompt_sha256", "system_prompt", "tool_catalog_version",
            "tool_catalog_sha256", "model_visible_tools",
        },
        "controller": {"max_iterations", "max_repair_cycles", "total_timeout_seconds", "recovery_policy"},
        "limits": {
            "tool_test_timeout_seconds", "max_observation_chars", "max_patch_chars",
            "repository_context_max_chars", "repository_context_max_files",
            "repository_context_max_symbols",
        },
        "retrieval": {"strategy", "configuration_source", "semantic_enabled", "hybrid_enabled"},
        "sandbox": {
            "network_mode", "user", "read_only_root_filesystem", "capabilities_dropped",
            "no_new_privileges", "memory", "cpus", "pids_limit",
            "only_staged_worktree_writable", "docker_socket_mounted", "host_home_mounted",
            "ssh_agent_mounted", "credentials_forwarded", "model_selected_commands",
        },
        "evaluation": {
            "taxonomy_id", "taxonomy_version", "taxonomy_sha256", "official_harness_authoritative",
            "prediction_format", "cost_policy",
        },
        "protocol": {
            "one_attempt_per_instance", "run_order", "manual_intervention", "post_hoc_tuning",
            "instance_substitution", "agent_rerun", "harness_retry_on_infrastructure_failure",
        },
    }
    for field, expected in nested_fields.items():
        actual = value[field]
        if not isinstance(actual, dict) or set(actual) != expected:
            raise ValueError(f"behavioral {field} has missing or unknown fields: {sorted(set(actual) ^ expected) if isinstance(actual, dict) else 'not an object'}")

    instances = value["instances"]
    if not isinstance(instances, list) or tuple(item.get("instance_id") for item in instances) != INSTANCE_IDS:
        raise ValueError("behavioral pilot must contain exactly the two frozen instances in order")
    expected_instance_fields = {
        "instance_id", "upstream_repository", "base_commit", "image",
        "trusted_test_plan", "trusted_test_plan_hash", "setup_artifacts",
    }
    plans: dict[str, TrustedTestPlan] = {}
    for item in instances:
        if not isinstance(item, dict) or set(item) != expected_instance_fields:
            raise ValueError("behavioral instance has missing or unknown fields")
        if not isinstance(item["base_commit"], str) or len(item["base_commit"]) != 40:
            raise ValueError("behavioral base commit must be a full commit")
        if "@sha256:" not in item["image"]:
            raise ValueError("behavioral image must be pinned by digest")
        plan = TrustedTestPlan.from_dict(item["trusted_test_plan"])
        if plan.content_hash != item["trusted_test_plan_hash"]:
            raise ValueError(f"trusted test-plan hash mismatch for {item['instance_id']}")
        plans[item["instance_id"]] = plan
        for artifact in item["setup_artifacts"]:
            if set(artifact) != {"path", "sha256", "source", "oracle_content"} or artifact["oracle_content"] is not False:
                raise ValueError("setup artifacts must be non-oracle and strictly described")

    if value["protocol"] != {
        "one_attempt_per_instance": True,
        "run_order": list(INSTANCE_IDS),
        "manual_intervention": False,
        "post_hoc_tuning": False,
        "instance_substitution": False,
        "agent_rerun": False,
        "harness_retry_on_infrastructure_failure": False,
    }:
        raise ValueError("behavioral protocol must freeze one untuned attempt per instance")
    if value["retrieval"].get("strategy") != "structural" or value["retrieval"].get("semantic_enabled") or value["retrieval"].get("hybrid_enabled"):
        raise ValueError("behavioral pilot permits structural retrieval only")
    if tuple(value["agent"].get("model_visible_tools", ())) != tuple(
        definition.name for definition in PUBLIC_TOOL_DEFINITIONS
    ):
        raise ValueError("behavioral pilot tool surface must equal the canonical six tools")
    if value["agent"].get("system_prompt") != SYSTEM_PROMPT:
        raise ValueError("frozen system prompt does not equal the runtime prompt")
    if value["agent"].get("system_prompt_sha256") != _sha256_bytes(SYSTEM_PROMPT.encode()):
        raise ValueError("frozen system-prompt hash mismatch")
    if value["agent"].get("tool_catalog_sha256") != _sha256_json(_tool_catalog()):
        raise ValueError("frozen tool-catalog hash mismatch")
    if value["provider"].get("provider") != "openai_compatible" or value["provider"].get("credentials_required") is not False:
        raise ValueError("pilot provider must be the frozen credential-free compatible endpoint")
    if (
        value["provider"].get("endpoint") != "http://127.0.0.1:11434/v1"
        or value["provider"].get("endpoint_api") != "Responses"
        or value["provider"].get("sdk_retry_limit") != 0
        or value["provider"].get("sdk_timeout_seconds") != 60
    ):
        raise ValueError("pilot provider endpoint, API, timeout, and SDK retry policy are frozen")
    if value["official_environment"].get("harness_retry_limit") != 0:
        raise ValueError("pilot does not permit post-result harness retries")
    if value["controller"].get("max_iterations") != 30 or value["controller"].get("max_repair_cycles") != 3:
        raise ValueError("pilot controller budgets must equal the frozen V1 defaults")
    if value["controller"].get("total_timeout_seconds") != 300:
        raise ValueError("pilot total deadline must equal the frozen V1 default")
    if value["limits"] != {
        "tool_test_timeout_seconds": 120,
        "max_observation_chars": 20_000,
        "max_patch_chars": 50_000,
        "repository_context_max_chars": 12_000,
        "repository_context_max_files": 40,
        "repository_context_max_symbols": 240,
    }:
        raise ValueError("pilot observation, context, patch, and tool limits are frozen")
    if value["sandbox"] != {
        "network_mode": "none",
        "user": "10001:10001",
        "read_only_root_filesystem": True,
        "capabilities_dropped": ["ALL"],
        "no_new_privileges": True,
        "memory": "512m",
        "cpus": "1.0",
        "pids_limit": 128,
        "only_staged_worktree_writable": True,
        "docker_socket_mounted": False,
        "host_home_mounted": False,
        "ssh_agent_mounted": False,
        "credentials_forwarded": False,
        "model_selected_commands": False,
    }:
        raise ValueError("pilot sandbox security configuration is frozen")
    recovery = RecoveryPolicy.from_mapping(value["controller"].get("recovery_policy"))
    taxonomy = load_failure_taxonomy()
    taxonomy_path = Path(taxonomy.source)
    if value["evaluation"].get("taxonomy_sha256") != _sha256_bytes(taxonomy_path.read_bytes()):
        raise ValueError("frozen failure-taxonomy hash mismatch")

    forbidden_tokens = ("python_callable", "shell_fragment", "import_path", "arbitrary_command")
    serialized = _canonical_json(value).casefold()
    if any(token in serialized for token in forbidden_tokens):
        raise ValueError("behavioral configuration must remain data-only")
    content_hash = _sha256_json(value)
    value["resolved_test_plans"] = plans
    value["resolved_recovery_policy"] = recovery
    value["content_hash"] = content_hash
    return value


def _safe_task(task_root: Path, instance_id: str, expected_commit: str, expected_repository: str) -> dict[str, str]:
    path = task_root / "tasks" / instance_id / "task.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("id") != instance_id or data.get("base_commit") != expected_commit:
        raise ValueError("task metadata does not match the frozen instance and base commit")
    if data.get("upstream_repository") != expected_repository:
        raise ValueError("task repository does not match the frozen configuration")
    issue = data.get("issue_description")
    if not isinstance(issue, str) or not issue.strip():
        raise ValueError("task has no genuine issue text")
    return {
        "issue": issue,
        "upstream_url": str(data["upstream_url"]),
        "task_path": str(path.resolve()),
        "reference_path": str((path.parent / data["reference_patch"]).resolve()),
        "test_patch_path": str((path.parent / data["upstream_test_patch"]).resolve()),
        "oracle_blob": _canonical_json(
            {
                "expected_fix_files": data.get("expected_fix_files"),
                "fail_to_pass": data.get("fail_to_pass"),
                "pass_to_pass": data.get("pass_to_pass"),
            }
        ),
    }


def _run_checked(command: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {command[0]}")
    return completed


def stage_fresh_checkout(source: Path, target: Path, expected_commit: str) -> None:
    if target.exists():
        raise FileExistsError(f"fresh staging target already exists: {target}")
    _run_checked(["git", "clone", "--quiet", "--no-hardlinks", str(source.resolve()), str(target)])
    _run_checked(["git", "-C", str(target), "checkout", "--quiet", "--detach", expected_commit])
    actual = _run_checked(["git", "-C", str(target), "rev-parse", "HEAD"]).stdout.strip()
    if actual != expected_commit:
        raise RuntimeError("fresh staging revision mismatch")
    for root, directories, files in os.walk(target):
        for name in directories:
            path = Path(root) / name
            path.chmod(path.stat().st_mode | stat.S_IWGRP | stat.S_IWOTH | stat.S_IXGRP | stat.S_IXOTH)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IWGRP | stat.S_IWOTH)


def stage_setup_artifacts(workspace: Path, image: str, artifacts: list[dict[str, Any]]) -> None:
    if not artifacts:
        return
    created = _run_checked(["docker", "create", "--platform", "linux/amd64", image, "sleep", "infinity"]).stdout.strip()
    try:
        for artifact in artifacts:
            destination = workspace / artifact["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            _run_checked(["docker", "cp", f"{created}:/testbed/{artifact['path']}", str(destination)])
            if _sha256_bytes(destination.read_bytes()).removeprefix("sha256:") != artifact["sha256"]:
                raise RuntimeError(f"setup artifact hash mismatch: {artifact['path']}")
            destination.chmod(destination.stat().st_mode | stat.S_IWGRP | stat.S_IWOTH)
    finally:
        subprocess.run(["docker", "rm", "-f", created], text=True, capture_output=True)


def _scan_visible(value: object, forbidden_blobs: tuple[str, ...]) -> dict[str, Any]:
    return scan_contamination(
        Path.cwd() / ".empty-contamination-boundary",
        visible_artifacts=[value],
        forbidden_names=FORBIDDEN_NAMES,
        forbidden_blobs=forbidden_blobs,
    )


def run_preflight_gates(
    sandbox: SWEbenchSandbox,
    *,
    issue: str,
    forbidden_blobs: tuple[str, ...],
    forbidden_host_roots: tuple[Path, ...],
) -> dict[str, Any]:
    security = sandbox.inspect_security()
    prompt = build_transcript(issue, [])
    list_result = sandbox.invoke("list_files", {})
    search_result = sandbox.invoke("search_code", {"query": "class", "path": ".", "regex": False})
    diff_result = sandbox.invoke("git_diff", {})
    visible_tool_outputs = {"list_files": list_result, "search_code": search_result, "git_diff": diff_result}
    workspace_scan = scan_contamination(
        sandbox.workspace,
        forbidden_names=FORBIDDEN_NAMES,
        forbidden_blobs=forbidden_blobs,
    )
    prompt_scan = _scan_visible(prompt, forbidden_blobs)
    context_scan = _scan_visible(list_result.get("result", {}).get("repository_context", {}), forbidden_blobs)
    outputs_scan = _scan_visible(visible_tool_outputs, forbidden_blobs)
    schemas_scan = _scan_visible(provider_tool_schemas(), forbidden_blobs)
    environment_names = security.get("evidence", {}).get("environment_variable_names", [])
    sensitive_environment = [
        name for name in environment_names
        if any(term in name.casefold() for term in ("key", "token", "secret", "password", "credential", "ssh", "openai"))
    ]
    assertions = {
        "staged_worktree_names_and_blobs_absent": workspace_scan["passed"],
        "model_prompt_clean": prompt_scan["passed"],
        "repository_context_clean": context_scan["passed"],
        "sampled_read_only_tool_outputs_clean": outputs_scan["passed"],
        "tool_schemas_clean": schemas_scan["passed"],
        "container_environment_credentials_absent": not sensitive_environment,
        "forbidden_host_roots_not_mounted": security["assertions"].get("forbidden_host_roots_not_mounted") is True,
        "canonical_six_tool_surface": tuple(definition.name for definition in PUBLIC_TOOL_DEFINITIONS) == (
            "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff",
        ),
        "preflight_tools_executed_without_error": all(item.get("ok") for item in visible_tool_outputs.values()),
        "forbidden_roots_configured": len(forbidden_host_roots) >= 3,
    }
    contamination = {
        "passed": all(assertions.values()),
        "assertions": assertions,
        "assertions_passed": sum(assertions.values()),
        "assertions_total": len(assertions),
        "workspace_scan": workspace_scan,
        "prompt_scan": prompt_scan,
        "repository_context_scan": context_scan,
        "tool_output_scan": outputs_scan,
        "tool_schema_scan": schemas_scan,
        "sensitive_environment_variable_names": sensitive_environment,
        "forbidden_blob_count": len(forbidden_blobs),
    }
    return {"passed": security["passed"] and contamination["passed"], "security": security, "contamination": contamination}


def verify_frozen_local_model(profile: Mapping[str, Any]) -> dict[str, Any]:
    provider = profile["provider"]
    listed = _run_checked(["ollama", "list"], timeout=30).stdout.splitlines()
    row = next((line for line in listed if line.split() and line.split()[0] == provider["model"]), None)
    if row is None:
        raise RuntimeError("frozen Ollama model is not installed")
    short_id = row.split()[1]
    expected = provider["model_manifest_sha256"].removeprefix("sha256:")
    if not expected.startswith(short_id):
        raise RuntimeError("installed Ollama model does not match the frozen manifest")
    return {"passed": True, "model": provider["model"], "manifest_sha256": provider["model_manifest_sha256"]}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_value(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def execute_instance(
    profile: dict[str, Any],
    *,
    instance: dict[str, Any],
    task_root: Path,
    source_checkout: Path,
    staging_root: Path,
    output_root: Path,
    runner_source_root: Path,
) -> dict[str, Any]:
    instance_id = instance["instance_id"]
    task = _safe_task(task_root, instance_id, instance["base_commit"], instance["upstream_repository"])
    run_id = f"p2-behavioral-{instance_id}-{uuid.uuid4().hex[:8]}"
    run_directory = output_root / instance_id
    if run_directory.exists():
        raise FileExistsError(f"behavioral result already exists; rerun forbidden: {run_directory}")
    run_directory.mkdir(parents=True)
    workspace = staging_root / run_id
    stage_fresh_checkout(source_checkout, workspace, instance["base_commit"])
    stage_setup_artifacts(workspace, instance["image"], instance["setup_artifacts"])

    forbidden_blobs = (
        Path(task["reference_path"]).read_text(encoding="utf-8"),
        Path(task["test_patch_path"]).read_text(encoding="utf-8"),
        task["oracle_blob"],
    )
    forbidden_roots = (
        runner_source_root.resolve(),
        task_root.resolve(),
        Path(task["task_path"]).parent.resolve(),
        Path("/tmp/repopilot-swebench-official-7a21e057").resolve(),
        Path("/tmp/repopilot-swebench-hf-cache").resolve(),
    )
    plan = profile["resolved_test_plans"][instance_id]
    sandbox = SWEbenchSandbox(
        workspace,
        instance_id=instance_id,
        image=instance["image"],
        expected_base_commit=instance["base_commit"],
        test_plan=plan,
        issue=task["issue"],
        command_timeout_seconds=profile["limits"]["tool_test_timeout_seconds"],
        config=SandboxConfig(
            memory=profile["sandbox"]["memory"],
            cpus=profile["sandbox"]["cpus"],
            pids_limit=profile["sandbox"]["pids_limit"],
        ),
        forbidden_host_roots=forbidden_roots,
        runner_source_root=runner_source_root,
    )
    try:
        sandbox.start()
        gates = run_preflight_gates(
            sandbox,
            issue=task["issue"],
            forbidden_blobs=forbidden_blobs,
            forbidden_host_roots=forbidden_roots,
        )
        _write_json(run_directory / "preflight.json", gates)
        if not gates["passed"]:
            return {
                "instance_id": instance_id,
                "status": "gate_failure",
                "model_executed": False,
                "gates": gates,
                "run_directory": str(run_directory),
            }

        from openai import OpenAI

        provider = profile["provider"]
        client = OpenAI(
            base_url=provider["endpoint"],
            api_key="not-required",
            timeout=float(provider["sdk_timeout_seconds"]),
            max_retries=int(provider["sdk_retry_limit"]),
        )
        model = OpenAICompatibleModel(
            provider["model"],
            base_url=provider["endpoint"],
            api_key="not-required",
            client=client,
        )
        recorder = TrajectoryRecorder(
            run_directory / "trajectory.jsonl",
            run_id=run_id,
            metadata={
                "issue": task["issue"],
                "repository": "fresh-staged-workspace",
                "test_command": list(plan.command),
                "model": model.metadata,
                "limits": profile["controller"] | profile["limits"],
                "sandbox": profile["sandbox"],
                "evaluation_profile": {
                    "profile_id": profile["profile_id"],
                    "profile_version": profile["profile_version"],
                    "content_hash": profile["content_hash"],
                },
                "tool_transport": "direct",
                "retrieval": {"strategy": "structural"},
                "instance_id": instance_id,
            },
        )
        registry = ToolRegistry(sandbox)  # type: ignore[arg-type]
        loop = AgentLoop(
            issue=task["issue"],
            model=model,
            tools=registry,
            recorder=recorder,
            max_iterations=profile["controller"]["max_iterations"],
            max_repair_cycles=profile["controller"]["max_repair_cycles"],
            total_timeout_seconds=profile["controller"]["total_timeout_seconds"],
            recovery_policy=profile["resolved_recovery_policy"],
        )
        started = time.perf_counter()
        result = loop.run(run_id, started_at=started)
    finally:
        sandbox.close()

    trace_summary = summarize_trace(run_directory / "trajectory.jsonl")
    run_payload = {
        **asdict(result),
        "instance_id": instance_id,
        "base_commit": instance["base_commit"],
        "profile": {
            "profile_id": profile["profile_id"],
            "profile_version": profile["profile_version"],
            "content_hash": profile["content_hash"],
            "resolved_configuration": {
                key: value for key, value in profile.items()
                if key not in {"resolved_test_plans", "resolved_recovery_policy"}
            },
        },
        "preflight": gates,
        "trace_summary": trace_summary,
    }
    run_payload["trace_reconciliation"] = reconcile_summary(trace_summary, run_payload)
    events = list(TraceReader(run_directory / "trajectory.jsonl"))
    run_payload["failure_analysis"] = classify_failures(events)
    run_payload["prediction"] = {
        "instance_id": instance_id,
        "model_name_or_path": f"repopilot/{provider['model']}@{provider['model_manifest_sha256']}",
        "model_patch": result.final_diff,
    }
    _write_json(run_directory / "run.json", run_payload)
    (run_directory / "model.patch").write_text(result.final_diff, encoding="utf-8")
    _write_json(run_directory / "trace-summary.json", trace_summary)
    return {
        "instance_id": instance_id,
        "status": "prediction_generated",
        "model_executed": True,
        "run_id": run_id,
        "run_directory": str(run_directory),
        "prediction": run_payload["prediction"],
        "run": run_payload,
    }


def execute_pilot(
    profile_path: Path,
    *,
    task_root: Path,
    source_root: Path,
    staging_root: Path,
    output_root: Path,
    runner_source_root: Path,
) -> dict[str, Any]:
    profile = load_behavioral_profile(profile_path)
    model_check = verify_frozen_local_model(profile)
    source_map = {
        "pallets__flask-5014": source_root / "repopilot-sweb-stage-pallets__flask-5014",
        "pytest-dev__pytest-10051": source_root / "repopilot-sweb-stage-pytest-dev__pytest-10051",
    }
    if output_root.exists():
        raise FileExistsError(f"pilot output already exists; rerun forbidden: {output_root}")
    output_root.mkdir(parents=True)
    cases: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for instance in profile["instances"]:
        case = execute_instance(
            profile,
            instance=instance,
            task_root=task_root,
            source_checkout=source_map[instance["instance_id"]],
            staging_root=staging_root,
            output_root=output_root,
            runner_source_root=runner_source_root,
        )
        cases.append(case)
        if case.get("prediction") is not None:
            predictions.append(case["prediction"])
    prediction_path = output_root / "predictions.jsonl"
    prediction_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in predictions),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "track": TRACK,
        "profile_path": str(profile_path),
        "profile_content_hash": profile["content_hash"],
        "model_check": model_check,
        "prediction_path": str(prediction_path),
        "attempted_instances": [case["instance_id"] for case in cases if case.get("model_executed")],
        "cases": [
            {
                "instance_id": case["instance_id"],
                "status": case["status"],
                "model_executed": case["model_executed"],
                "run_id": case.get("run_id"),
                "run_directory": case["run_directory"],
            }
            for case in cases
        ],
    }
    _write_json(output_root / "pilot-manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen two-case SWE-bench behavioral pilot once")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("/tmp"))
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner-source-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = execute_pilot(
        args.profile.resolve(),
        task_root=args.tasks.resolve(),
        source_root=args.source_root.resolve(),
        staging_root=args.staging_root.resolve(),
        output_root=args.output.resolve(),
        runner_source_root=args.runner_source_root.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
