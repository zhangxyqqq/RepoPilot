from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import logging
import multiprocessing
from pathlib import Path
import threading
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from repopilot.service.api import create_app
from repopilot.service.schemas import TaskCreate
from repopilot.service.store import Store, IdempotencyConflict
from repopilot.service.worker import Worker
from repopilot.trajectory.schema import stable_trace_id

pytestmark = pytest.mark.postgres
PAYLOAD = {"repository": "example", "issue": "Inspect the repository", "profile": "default"}
SUCCESS = {"success": True, "stop_reason": "tests_passed", "final_diff": "", "changed_files": []}


def submit(settings, key=None):
    return Store(settings).submit(PAYLOAD, key, uuid4())[0]


def client(settings):
    return TestClient(create_app(settings), headers={"Authorization": f"Bearer {settings.api_token}"})


def expire(settings, run_id):
    with Store(settings).connect() as db:
        db.execute("UPDATE runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (run_id,))


def race_submit(settings, barrier, queue):
    barrier.wait(timeout=15)
    task, created = Store(settings).submit(PAYLOAD, "concurrent", uuid4())
    queue.put((str(task["id"]), created))


def race_claim(settings, barrier, release, queue):
    barrier.wait(timeout=15)
    claim = Store(settings).claim(str(uuid4()))
    queue.put(str(claim.run["id"]) if claim else None)
    if claim:
        release.wait(timeout=15)
        claim.release()


def crash_claim(settings, queue):
    claim = Store(settings).claim("dead-process")
    queue.put(str(claim.run["id"]))
    # Parent kills this real process while it holds the task's execution lock.
    threading.Event().wait(30)


def test_api_create_pending_and_validation(settings):
    with client(settings) as api:
        assert api.get("/healthz").status_code == api.get("/readyz").status_code == 200
        response = api.post("/v1/tasks", json=PAYLOAD)
        assert response.status_code == 202
        task = response.json()
        assert task["status"] == "QUEUED" and task["runs"] == []
        assert response.headers["location"] == f"/v1/tasks/{task['id']}"
        assert api.get(response.headers["location"]).json()["id"] == task["id"]
        assert api.get(response.headers["location"] + "/result").status_code == 409
        assert api.get(response.headers["location"] + "/trace").json()["runs"] == []
        assert api.get(f"/v1/tasks/{uuid4()}").status_code == 404
        assert api.get("/v1/tasks/not-a-uuid").status_code == 422


@pytest.mark.parametrize("body", [{}, {**PAYLOAD, "issue": " "}, {**PAYLOAD, "repository": "../escape"},
    {**PAYLOAD, "repository": "/etc"}, {**PAYLOAD, "repository": "missing"},
    {**PAYLOAD, "api_key": "do-not-echo"}, {**PAYLOAD, "issue": "x" * 20001},
    {**PAYLOAD, "profile": "arbitrary-shell"}, {**PAYLOAD, "issue": "api_key=secret-value"}])
def test_invalid_payload(settings, body):
    with client(settings) as api:
        response = api.post("/v1/tasks", json=body)
        assert response.status_code == 422
        assert "do-not-echo" not in response.text and "secret-value" not in response.text
    with Store(settings).connect() as db:
        assert db.execute("SELECT count(*) n FROM tasks").fetchone()["n"] == 0


def test_authentication(settings):
    with TestClient(create_app(settings)) as api:
        assert api.post("/v1/tasks", json=PAYLOAD).status_code == 401
        assert api.get(f"/v1/tasks/{uuid4()}").status_code == 401
        assert api.get("/healthz").status_code == 200


def test_symlink_escape(settings, tmp_path):
    (settings.workspace_root / "escape").symlink_to(tmp_path)
    with client(settings) as api:
        assert api.post("/v1/tasks", json={**PAYLOAD, "repository": "escape"}).status_code == 422


def test_idempotency_and_conflict_survive_source_removal(settings):
    with client(settings) as api:
        headers = {"Idempotency-Key": "stable-key"}
        first = api.post("/v1/tasks", json=PAYLOAD, headers=headers)
        same = api.post("/v1/tasks", json=PAYLOAD, headers=headers)
        assert first.status_code == 202 and same.status_code == 200
        assert first.json()["id"] == same.json()["id"]
        assert api.post("/v1/tasks", json={**PAYLOAD, "issue": "different"}, headers=headers).status_code == 409
        (settings.workspace_root / "example").rename(settings.workspace_root / "moved")
        assert api.post("/v1/tasks", json=PAYLOAD, headers=headers).json()["id"] == first.json()["id"]


def test_concurrent_duplicate_submissions_separate_processes(settings):
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(4), ctx.Queue()
    processes = [ctx.Process(target=race_submit, args=(settings, barrier, queue)) for _ in range(4)]
    for process in processes:
        process.start()
    try:
        results = [queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(20)
        assert all(process.exitcode == 0 for process in processes)
        assert len({task_id for task_id, _ in results}) == 1
        assert sum(created for _, created in results) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(5)


def test_concurrent_conflicting_submissions(settings):
    barrier = threading.Barrier(2)
    def create(issue):
        barrier.wait(timeout=10)
        try:
            return Store(settings).submit({**PAYLOAD, "issue": issue}, "conflict", uuid4())[1]
        except IdempotencyConflict:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(create, ["one", "two"]))
    assert sorted(map(str, outcomes)) == ["True", "conflict"]


def test_multiple_processes_race_for_one_task(settings):
    task = submit(settings)
    ctx = multiprocessing.get_context("spawn")
    barrier, release, queue = ctx.Barrier(4), ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=race_claim, args=(settings, barrier, release, queue)) for _ in range(4)]
    for process in processes:
        process.start()
    try:
        results = [queue.get(timeout=20) for _ in processes]
        assert sum(result is not None for result in results) == 1
        assert len(Store(settings).get(task["id"])["runs"]) == 1
    finally:
        release.set()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.kill()
                process.join(5)
        assert all(process.exitcode == 0 for process in processes)


def test_success_result_trace_and_api_restart(settings):
    task = submit(settings)
    assert Worker(settings, lambda _: SUCCESS).run_once()
    # Independent app instances and fresh DB connections read the same durable data.
    for _ in range(2):
        with client(settings) as api:
            status = api.get(f"/v1/tasks/{task['id']}").json()
            assert status["status"] == "SUCCEEDED" and status["completed_at"]
            run = status["runs"][0]
            assert run["attempt"] == 1 and run["completed_at"]
            assert run["trace_id"] == stable_trace_id(run["id"])
            result = api.get(f"/v1/tasks/{task['id']}/result").json()
            assert result["result"] == SUCCESS
            trace = api.get(f"/v1/tasks/{task['id']}/trace").json()
            assert trace["runs"][0]["id"] == run["id"]


def test_worker_failure_is_terminal_and_sanitized(settings):
    def fail(_):
        raise RuntimeError("password=never-store-this")
    task = submit(settings)
    assert Worker(settings, fail).run_once()
    with client(settings) as api:
        response = api.get(f"/v1/tasks/{task['id']}/result")
        assert response.json()["status"] == "FAILED"
        assert response.json()["error_code"] == "execution_error"
        assert "never-store-this" not in response.text
    assert not Worker(settings, fail).run_once()


def test_agent_negative_outcome_is_not_rewritten(settings):
    task = submit(settings)
    Worker(settings, lambda _: {"success": False, "stop_reason": "iteration_limit"}).run_once()
    result = Store(settings).get(task["id"])
    assert result["status"] == "FAILED"
    assert result["runs"][0]["stop_reason"] == "iteration_limit"


def test_independent_tasks_execute_concurrently_outside_http(settings):
    started = threading.Event()
    both = threading.Barrier(2)
    def execute(_):
        started.set()
        both.wait(timeout=10)
        return SUCCESS
    first = submit(settings)
    with ThreadPoolExecutor(2) as pool:
        worker1 = pool.submit(Worker(settings, execute).run_once)
        assert started.wait(timeout=10)
        # First agent is still blocked. HTTP submission/status remain responsive.
        with client(settings) as api:
            assert api.get(f"/v1/tasks/{first['id']}").json()["status"] == "RUNNING"
            second = api.post("/v1/tasks", json=PAYLOAD).json()
        worker2 = pool.submit(Worker(settings, execute).run_once)
        assert worker1.result(15) and worker2.result(15)
    assert Store(settings).get(first["id"])["status"] == "SUCCEEDED"
    from uuid import UUID
    assert Store(settings).get(UUID(second["id"]))["status"] == "SUCCEEDED"


def test_stale_lease_cannot_overlap_live_worker_and_completion_is_fenced(settings):
    task = submit(settings)
    store = Store(settings)
    old = store.claim("paused")
    expire(settings, old.run["id"])
    assert not store.heartbeat(old)
    assert Store(settings).claim("replacement") is None
    assert not store.finish(old, result=SUCCESS)
    old.release()
    new = store.claim("replacement")
    try:
        assert new.run["attempt"] == 2
        assert not store.finish(old, result=SUCCESS)
        assert store.finish(new, result=SUCCESS)
    finally:
        new.release()
    assert [r["status"] for r in store.get(task["id"])["runs"]] == ["ABANDONED", "SUCCEEDED"]


def test_process_death_releases_execution_lock(settings):
    task = submit(settings)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=crash_claim, args=(settings, queue))
    process.start()
    run_id = queue.get(timeout=20)
    process.kill()
    process.join(10)
    expire(settings, run_id)
    Worker(settings, lambda _: SUCCESS).run_once()
    state = Store(settings).get(task["id"])
    assert state["status"] == "SUCCEEDED"
    assert [r["status"] for r in state["runs"]] == ["ABANDONED", "SUCCEEDED"]


def test_bounded_recovery_attempts(settings):
    settings = replace(settings, max_attempts=1)
    task = submit(settings)
    old = Store(settings).claim("dead")
    expire(settings, old.run["id"])
    old.release()
    def never(_):
        pytest.fail("exhausted task must not execute")
    assert Worker(settings, never).run_once()
    state = Store(settings).get(task["id"])
    assert state["status"] == "FAILED" and len(state["runs"]) == 1
    assert state["runs"][0]["error_code"] == "attempts_exhausted"


def test_heartbeat_and_terminal_fence(settings):
    task = submit(settings)
    store = Store(settings)
    claim = store.claim("healthy")
    try:
        assert store.heartbeat(claim)
        assert store.finish(claim, result=SUCCESS)
        assert not store.heartbeat(claim)
        assert not store.finish(claim, result={"success": False})
    finally:
        claim.release()
    assert store.get(task["id"])["status"] == "SUCCEEDED"


def test_readiness_db_unavailable(settings):
    unavailable = replace(settings, database_url="postgresql://invalid:invalid@127.0.0.1:1/unavailable")
    with client(unavailable) as api:
        assert api.get("/healthz").status_code == 200
        response = api.get("/readyz")
        assert response.status_code == 503 and "invalid" not in response.text
        assert api.post("/v1/tasks", json=PAYLOAD).status_code == 503


def test_db_rejects_arbitrary_state_transition(settings):
    task = submit(settings)
    with pytest.raises(psycopg.Error):
        with Store(settings).connect() as db:
            db.execute("UPDATE tasks SET status='SUCCEEDED',completed_at=clock_timestamp() WHERE id=%s", (task["id"],))
    assert Store(settings).get(task["id"])["status"] == "QUEUED"


def test_correlated_structured_logs(settings, caplog):
    caplog.set_level(logging.INFO, logger="repopilot.service")
    request_id = str(uuid4())
    with client(settings) as api:
        task = api.post("/v1/tasks", json=PAYLOAD, headers={"X-Request-ID": request_id})
        assert task.headers["X-Request-ID"] == request_id
    Worker(settings, lambda _: SUCCESS, worker_id="known-worker").run_once()
    events = [json.loads(record.message) for record in caplog.records if record.name == "repopilot.service"]
    completed = next(e for e in events if e["event"] == "run_finished")
    assert completed["request_id"] == request_id
    assert completed["task_id"] == task.json()["id"]
    assert completed["worker_id"] == "known-worker" and completed["run_id"] and completed["trace_id"]
    assert PAYLOAD["issue"] not in caplog.text and settings.api_token not in caplog.text


@pytest.mark.docker
def test_real_agent_adapter_restricted_sandbox_and_trajectory(settings):
    task = submit(settings)
    assert Worker(settings).run_once()
    state = Store(settings).get(task["id"])
    assert state["status"] == "SUCCEEDED", state["runs"]
    run = state["runs"][0]
    events = [json.loads(line) for line in (settings.artifact_root / str(run["id"]) / "trajectory.jsonl").read_text().splitlines()]
    assert events[-1]["type"] == "run_finished"
    assert {e["trace_id"] for e in events} == {run["trace_id"]}
    assert (settings.workspace_root / "example" / "test_ok.py").read_text() == "def test_ok():\n    assert True\n"
    assert run["result"]["trajectory_path"] == f"{run['id']}/trajectory.jsonl"


def test_live_worker_renews_lease(settings):
    task = submit(settings)
    pulsed = threading.Event()
    worker = Worker(settings, lambda _: (pulsed.wait(10) and SUCCESS))
    original = worker.store.heartbeat
    owner = threading.current_thread()
    def heartbeat(claim):
        accepted = original(claim)
        if accepted and threading.current_thread() is not owner:
            pulsed.set()
        return accepted
    worker.store.heartbeat = heartbeat
    worker.run_once()
    assert pulsed.is_set()
    state = Store(settings).get(task["id"])
    assert state["status"] == "SUCCEEDED"
    assert state["runs"][0]["heartbeat_at"] > state["runs"][0]["started_at"]


def test_failed_heartbeat_never_commits_success(settings, monkeypatch):
    task = submit(settings)
    pulsed = threading.Event()
    worker = Worker(settings, lambda _: (pulsed.wait(10) and SUCCESS))
    original = worker.store.heartbeat
    owner = threading.current_thread()
    def heartbeat(claim):
        if threading.current_thread() is owner:
            return original(claim)
        raise psycopg.OperationalError("secret connection URL")
    # Release execution only once the worker has observed the failure. Releasing
    # inside the injected call races the worker's lost.set() exception handler.
    import repopilot.service.worker as worker_module
    original_event = worker_module.event
    def observed_event(name, **fields):
        original_event(name, **fields)
        if name == "heartbeat_failed":
            pulsed.set()
    monkeypatch.setattr(worker_module, "event", observed_event)
    worker.store.heartbeat = heartbeat
    worker.run_once()
    state = Store(settings).get(task["id"])
    assert state["status"] == "RUNNING"
    expire(settings, state["runs"][0]["id"])
    Worker(settings, lambda _: SUCCESS).run_once()
    assert Store(settings).get(task["id"])["status"] == "SUCCEEDED"


def hanging_worker(settings):
    Worker(settings, lambda _: threading.Event().wait(30)).run_once()


def test_hard_watchdog_terminates_execution_process(settings):
    settings = replace(settings, hard_timeout_seconds=3)
    task = submit(settings)
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=hanging_worker, args=(settings,))
    process.start()
    process.join(15)
    try:
        assert process.exitcode == 70
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    run = Store(settings).get(task["id"])["runs"][0]
    expire(settings, run["id"])
    Worker(settings, lambda _: SUCCESS).run_once()
    assert Store(settings).get(task["id"])["status"] == "SUCCEEDED"


def orphan_sandbox(settings, queue):
    from repopilot.config import SandboxConfig
    from repopilot.sandbox import DockerSandbox, stage_repository
    from repopilot.service.executor import sandbox_name
    claim = Store(settings).claim("orphan-owner")
    workspace = stage_repository(settings.repository("example"), settings.artifact_root / "orphan" / "workspace")
    sandbox = DockerSandbox(workspace, test_command=("python", "-m", "pytest", "-q"), command_timeout_seconds=30,
                            config=SandboxConfig(container_name=sandbox_name(claim.task["id"]), build_image=False))
    sandbox.start()
    queue.put((str(claim.run["id"]), sandbox.container_name))
    threading.Event().wait(30)


@pytest.mark.docker
def test_dead_worker_orphan_removed_before_real_agent_retry(settings):
    import subprocess
    task = submit(settings)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=orphan_sandbox, args=(settings, queue))
    process.start()
    try:
        run_id, container = queue.get(timeout=20)
        inspected = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        assert inspected["HostConfig"]["NetworkMode"] == "none"
        assert inspected["HostConfig"]["ReadonlyRootfs"] is True
        assert inspected["Config"]["User"] == "10001:10001"
        assert [mount["Destination"] for mount in inspected["Mounts"]] == ["/workspace"]
        assert not any("API_KEY=" in entry for entry in inspected["Config"]["Env"])
        process.kill()
        process.join(5)
        expire(settings, run_id)
        assert Worker(settings).run_once()
        state = Store(settings).get(task["id"])
        assert state["status"] == "SUCCEEDED"
        assert [run["status"] for run in state["runs"]] == ["ABANDONED", "SUCCEEDED"]
        assert not subprocess.check_output(["docker", "ps", "-aq", "--filter", f"name=^/{container}$"]).strip()
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        from repopilot.service.executor import sandbox_name
        subprocess.run(["docker", "rm", "-f", sandbox_name(task["id"])], capture_output=True)


def test_cleanup_failure_blocks_replacement_execution_even_at_attempt_limit(settings):
    settings = replace(settings, max_attempts=1)
    task = submit(settings)
    claim = Store(settings).claim("dead")
    expire(settings, claim.run["id"])
    claim.release()
    class UnavailableDocker:
        def cleanup(self, _):
            raise RuntimeError("Docker unavailable")
        def __call__(self, _):
            pytest.fail("must not execute without cleanup")
    Worker(settings, UnavailableDocker()).run_once()
    assert Store(settings).get(task["id"])["status"] == "RUNNING"
    expire(settings, claim.run["id"])
    Worker(settings, lambda _: pytest.fail("attempt budget exhausted")).run_once()
    assert Store(settings).get(task["id"])["status"] == "FAILED"


def test_locked_candidate_does_not_starve_independent_task(settings):
    first = submit(settings)
    old = Store(settings).claim("paused")
    expire(settings, old.run["id"])
    second = submit(settings)
    try:
        assert Worker(settings, lambda _: SUCCESS).run_once()
        assert Store(settings).get(first["id"])["status"] == "RUNNING"
        assert Store(settings).get(second["id"])["status"] == "SUCCEEDED"
    finally:
        old.release()


def test_database_unique_active_attempt_and_run_terminal_transition(settings):
    submit(settings)
    store = Store(settings)
    claim = store.claim("one")
    try:
        with pytest.raises(psycopg.errors.UniqueViolation):
            with store.connect() as db:
                db.execute("""INSERT INTO runs(id,task_id,attempt,status,worker_id,trace_id,lease_expires_at)
                    VALUES (%s,%s,2,'RUNNING','two','other',clock_timestamp()+interval '1 minute')""",
                    (uuid4(), claim.task["id"]))
        assert store.finish(claim, result=SUCCESS)
        with pytest.raises(psycopg.Error):
            with store.connect() as db:
                db.execute("UPDATE runs SET status='RUNNING',completed_at=NULL WHERE id=%s", (claim.run["id"],))
    finally:
        claim.release()


def test_known_control_secret_is_rejected_without_echo(settings):
    with client(settings) as api:
        response = api.post("/v1/tasks", json={**PAYLOAD, "issue": f"Inspect {settings.api_token}"})
        assert response.status_code == 422 and settings.api_token not in response.text


def test_worker_literal_secret_redaction_is_scoped(settings, monkeypatch):
    from repopilot.service.executor import AgentExecutor
    from repopilot.trajectory import redact_text, TrajectoryRecorder
    secret = "provideropaquecredential1234"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    executor = AgentExecutor(settings)
    path = settings.artifact_root / "test-trace.jsonl"
    def execute(_):
        recorder = TrajectoryRecorder(path, run_id="redaction-check", metadata={"note": secret})
        recorder.record("model_error", error=secret)
        return redact_text(secret)
    monkeypatch.setattr(executor, "execute", execute)
    assert executor(None) == "[REDACTED]"
    assert secret not in path.read_text()
    assert redact_text(secret) == secret  # Existing CLI redaction behavior is unchanged.


def test_service_settings_require_postgres_and_separate_roots(settings):
    with pytest.raises(ValueError):
        replace(settings, database_url="sqlite:///tasks.db")
    with pytest.raises(ValueError):
        replace(settings, artifact_root=settings.workspace_root / "artifacts")
    with pytest.raises(ValueError):
        replace(settings, api_token="short")


def test_readiness_requires_applied_revision(settings):
    with Store(settings).connect() as db:
        db.execute("DELETE FROM alembic_version")
    try:
        with client(settings) as api:
            assert api.get("/readyz").status_code == 503
            assert api.get("/healthz").status_code == 200
    finally:
        with Store(settings).connect() as db:
            db.execute("INSERT INTO alembic_version(version_num) VALUES ('0002')")
