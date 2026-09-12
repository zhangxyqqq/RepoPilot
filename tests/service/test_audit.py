"""Post-implementation regressions: exercise failures the original suite missed."""
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from repopilot.service.api import create_app
from repopilot.service.executor import AgentExecutor, sandbox_name
from repopilot.service.store import Store
from repopilot.service.worker import Worker
from repopilot.trajectory import TrajectoryRecorder
from repopilot.trajectory.schema import secret_redaction

pytestmark = pytest.mark.postgres
PAYLOAD = {"repository": "example", "issue": "Inspect repository", "profile": "default"}
SUCCESS = {"success": True, "stop_reason": "tests_passed"}


def submit(settings):
    return Store(settings).submit(PAYLOAD, None, uuid4())[0]


def expire(settings, run_id):
    with Store(settings).connect() as db:
        db.execute("UPDATE runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (run_id,))


def test_lost_lease_during_cleanup_never_starts_agent(settings):
    task = submit(settings)
    calls = []
    class SlowCleanup:
        def cleanup(self, claim):
            expire(settings, claim.run['id'])
        def __call__(self, claim):
            calls.append(claim.run['id'])
            return SUCCESS
    Worker(settings, SlowCleanup()).run_once()
    assert calls == []
    assert Store(settings).get(task['id'])['status'] == 'RUNNING'


def test_scripted_worker_never_runs_queued_provider_task(settings, monkeypatch):
    task = submit(replace(settings, scripted=False))
    claim = Store(settings).claim('scripted-worker')
    calls = []
    def provider(config):
        calls.append(config)
        raise ValueError('do not call a real provider')
    monkeypatch.setattr('repopilot.service.executor.create_model', provider)
    try:
        with pytest.raises(ValueError):
            AgentExecutor(settings)(claim)
        assert calls == []
    finally:
        claim.release()


@pytest.mark.parametrize('text', ['bad\x00value', 'bad\ud800value'])
def test_invalid_postgres_text_is_client_error(settings, text):
    with TestClient(create_app(settings), headers={'Authorization': f'Bearer {settings.api_token}'}) as api:
        response = api.post('/v1/tasks', content=json.dumps({**PAYLOAD, 'issue': text}),
                            headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
    with Store(settings).connect() as db:
        assert db.execute('SELECT count(*) n FROM tasks').fetchone()['n'] == 0


def test_literal_secret_in_mapping_key_never_reaches_trace(settings):
    secret = 'opaque-provider-secret-123456'
    path = settings.artifact_root / 'audit.jsonl'
    with secret_redaction([secret]):
        TrajectoryRecorder(path, run_id='audit', metadata={secret: 'value'})
    assert secret not in path.read_text()


def slow_agent(settings, queue):
    class Executor(AgentExecutor):
        def __call__(self, claim):
            queue.put((str(claim.run['id']), str(claim.task['id'])))
            return super().__call__(claim)
    Worker(settings, Executor(settings)).run_once()


def wait_for_file(path, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(.02)
    raise AssertionError(f'fixture did not reach barrier: {path}')


@pytest.mark.docker
def test_inflight_docker_exec_keeps_task_locked_after_worker_sigkill(settings):
    # Real pytest inside real Docker exec is paused at a file barrier. Killing
    # only the worker must not allow another owner while the Docker CLI survives.
    source = settings.workspace_root / 'example' / 'test_ok.py'
    source.write_text('''from pathlib import Path
import time
def test_gate():
    Path("entered").write_text("ready")
    deadline = time.monotonic() + 25
    while not Path("release").exists():
        assert time.monotonic() < deadline
        time.sleep(.02)
''')
    task = submit(settings)
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    process = ctx.Process(target=slow_agent, args=(settings, queue))
    process.start()
    replacement = None
    workspace = None
    try:
        run_id, _ = queue.get(timeout=20)
        workspace = settings.artifact_root / run_id / 'workspace'
        wait_for_file(workspace / 'entered')
        process.kill()
        process.join(10)
        expire(settings, run_id)
        replacement = Store(settings).claim('replacement')
        assert replacement is None, 'Docker CLI survived worker death but lost execution fencing'
        (workspace / 'release').touch()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and replacement is None:
            replacement = Store(settings).claim('replacement')
            if replacement is None:
                time.sleep(.02)
        assert replacement is not None, 'completed helper did not release its lock'
        AgentExecutor(settings).cleanup(replacement)
        assert Store(settings).finish(replacement, result=SUCCESS)
    finally:
        if replacement:
            replacement.release()
        if workspace and workspace.exists():
            (workspace / 'release').touch()
        if process.is_alive():
            process.kill()
            process.join(5)
        # Remove only the known test task's sandbox; never leave a blocked child.
        subprocess.run(['docker', 'rm', '-f', sandbox_name(task['id'])], capture_output=True, timeout=30)


@pytest.mark.docker
def test_delayed_docker_command_cannot_target_reused_container_name(settings, monkeypatch):
    from repopilot.config import SandboxConfig
    from repopilot.sandbox import DockerSandbox, stage_repository
    import repopilot.sandbox.docker as docker_module
    (settings.workspace_root / 'example' / 'value.py').write_text('value = 0\n')
    workspaces = [stage_repository(settings.repository('example'), settings.artifact_root / str(i)) for i in range(2)]
    name = sandbox_name(uuid4())
    sandboxes = [DockerSandbox(workspace, test_command=('python', '-m', 'pytest', '-q'), command_timeout_seconds=30,
                    config=SandboxConfig(container_name=name, build_image=False)) for workspace in workspaces]
    delayed = []
    # Pause exactly between command construction and OS dispatch. The final command
    # is executed against the real daemon after the old container has been removed.
    def capture(command, **kwargs):
        delayed.append(command)
        return subprocess.CompletedProcess(command, 0, '{"ok":true,"result":{}}', '')
    try:
        sandboxes[0].start()
        with monkeypatch.context() as patch:
            target = 'run_process' if hasattr(docker_module, 'run_process') else 'subprocess.run'
            patch.setattr(f'repopilot.sandbox.docker.{target}', capture)
            sandboxes[0].invoke('apply_patch', {'patch': '*** Begin Patch\n*** Update File: value.py\n@@\n-value = 0\n+value = 99\n*** End Patch'})
        sandboxes[0].close()
        sandboxes[1].start()
        late = subprocess.run(delayed[0], capture_output=True, text=True, timeout=30)
        assert late.returncode != 0, 'late operation was routed to a replacement container'
        assert (workspaces[1] / 'value.py').read_text() == 'value = 0\n'
    finally:
        for sandbox in sandboxes:
            sandbox.close()


def blocked_dispatcher(settings):
    worker = Worker(settings, lambda _: SUCCESS)
    worker.store.claim = lambda _: threading.Event().wait(30)
    worker.run_once()


def test_worker_watchdog_also_bounds_blocked_claim(settings):
    ctx = multiprocessing.get_context('spawn')
    process = ctx.Process(target=blocked_dispatcher, args=(replace(settings, hard_timeout_seconds=3),))
    process.start()
    process.join(7)
    try:
        assert process.exitcode == 70, 'claim path bypasses worker watchdog'
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)


def supervised_wait(settings, queue):
    import sys
    from repopilot.sandbox.process import run_process
    def execute(claim):
        marker = settings.artifact_root / 'helper-entered'
        queue.put(str(claim.run['id']))
        run_process([sys.executable, '-c',
                     'import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)',
                     str(marker)], timeout=2, capture_output=True)
        return SUCCESS
    Worker(settings, execute).run_once()


def test_docker_supervisor_timeout_survives_worker_death(settings):
    task = submit(settings)
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    process = ctx.Process(target=supervised_wait, args=(settings, queue))
    process.start()
    replacement = None
    try:
        run_id = queue.get(timeout=15)
        wait_for_file(settings.artifact_root / 'helper-entered')
        process.kill()
        process.join(5)
        expire(settings, run_id)
        replacement = Store(settings).claim('after-timeout')
        assert replacement is None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and replacement is None:
            replacement = Store(settings).claim('after-timeout')
            if replacement is None:
                time.sleep(.02)
        assert replacement is not None, 'helper timeout died with the worker'
        assert Store(settings).finish(replacement, result=SUCCESS)
        assert Store(settings).get(task['id'])['status'] == 'SUCCEEDED'
    finally:
        if replacement:
            replacement.release()
        if process.is_alive():
            process.kill()
            process.join(5)


def test_commit_ack_loss_cannot_reexecute_completed_task(settings):
    import psycopg
    task = submit(settings)
    executions = []
    def execute(claim):
        executions.append(claim.run['id'])
        return SUCCESS
    worker = Worker(settings, execute)
    connect = worker.store.connect
    class CommitAckLost:
        def __init__(self):
            self.context = connect()
            self.terminal = False
        def __enter__(self):
            self.connection = self.context.__enter__()
            return self
        def execute(self, sql, params=None):
            if 'UPDATE tasks SET status=%s' in sql:
                self.terminal = True
            return self.connection.execute(sql, params)
        def __exit__(self, *args):
            result = self.context.__exit__(*args)  # Real PostgreSQL commit.
            if self.terminal and args[0] is None:
                raise psycopg.OperationalError('injected lost commit acknowledgement')
            return result
    worker.store.connect = CommitAckLost
    assert worker.run_once()
    assert Store(settings).get(task['id'])['status'] == 'SUCCEEDED'
    assert not Worker(settings, execute).run_once()
    assert len(executions) == 1


def concurrent_worker(settings, barrier, queue):
    def execute(claim):
        queue.put((str(claim.task['id']), str(claim.run['id'])))
        barrier.wait(timeout=15)
        return SUCCESS
    Worker(settings, execute).run_once()


def test_independent_tasks_rendezvous_in_separate_worker_processes(settings):
    tasks = [submit(settings) for _ in range(2)]
    ctx = multiprocessing.get_context('spawn')
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=concurrent_worker, args=(settings, barrier, queue)) for _ in tasks]
    for process in processes:
        process.start()
    try:
        observed = [queue.get(timeout=20) for _ in tasks]
        assert {task_id for task_id, _ in observed} == {str(task['id']) for task in tasks}
        assert len({run_id for _, run_id in observed}) == 2
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert all(Store(settings).get(task['id'])['status'] == 'SUCCEEDED' for task in tasks)
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(5)


@pytest.mark.docker
def test_delayed_cleanup_cannot_remove_replacement_container(settings, monkeypatch):
    from repopilot.config import SandboxConfig
    from repopilot.sandbox import DockerSandbox, stage_repository
    task = submit(settings)
    claim = Store(settings).claim('cleanup-owner')
    workspaces = [stage_repository(settings.repository('example'), settings.artifact_root / str(i)) for i in range(2)]
    sandboxes = [DockerSandbox(workspace, test_command=('python', '-m', 'pytest', '-q'), command_timeout_seconds=30,
                  config=SandboxConfig(container_name=sandbox_name(task['id']), build_image=False)) for workspace in workspaces]
    captured = []
    import repopilot.service.executor as executor_module
    real = executor_module.run_process
    def delayed(command, **kwargs):
        if command[1:3] == ['rm', '-f']:
            captured.append(command)
            return subprocess.CompletedProcess(command, 0, '', '')
        return real(command, **kwargs)
    try:
        sandboxes[0].start()
        with monkeypatch.context() as patch:
            patch.setattr(executor_module, 'run_process', delayed)
            AgentExecutor(settings).cleanup(claim)
        assert len(captured) == 1
        sandboxes[0].close()
        sandboxes[1].start()
        subprocess.run(captured[0], capture_output=True, timeout=30)
        assert sandboxes[1].invoke('git_diff', {})['ok']
    finally:
        for sandbox in sandboxes:
            sandbox.close()
        claim.release()
