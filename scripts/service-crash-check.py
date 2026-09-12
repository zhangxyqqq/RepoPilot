"""Disruptive scripted-only local Compose test: SIGKILL one task-owning worker."""
import json
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location
import shutil
import subprocess
import time
from uuid import uuid4

spec = spec_from_file_location('smoke', Path(__file__).with_name('service-smoke.py'))
smoke = module_from_spec(spec)
spec.loader.exec_module(smoke)
request = smoke.request
compose = ['docker', 'compose', '--env-file', '.service.env']


def run():
    assert request('/readyz')['execution_mode'] == 'scripted', 'requires scripted API and workers'
    config = dict(line.split('=', 1) for line in Path('.service.env').read_text().splitlines() if line)
    name = 'audit-crash-' + uuid4().hex
    source = Path(config['REPOPILOT_WORKSPACE_ROOT']) / name
    artifacts = Path(config['REPOPILOT_ARTIFACT_ROOT'])
    source.mkdir()
    fixture = source / 'test_crash.py'
    fixture.write_text('from pathlib import Path\nimport time\ndef test_crash():\n    Path("entered").touch()\n    time.sleep(20)\n    assert False\n')
    task_id = None
    owner = None
    try:
        task = request('/v1/tasks', body={'repository': name, 'issue': 'Verify recovery after worker process death'})
        task_id = task['id']
        path = f'/v1/tasks/{task_id}'
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            task = request(path)
            if task['runs'] and (artifacts / task['runs'][-1]['id'] / 'workspace' / 'entered').exists():
                break
            assert task['status'] not in {'FAILED', 'SUCCEEDED'}, task
            time.sleep(.1)
        else:
            raise AssertionError('agent did not reach crash barrier')
        first = task['runs'][-1]
        # Identify the owning Compose worker by its structured startup event.
        owners = []
        for container in subprocess.check_output([*compose, 'ps', '-q', 'worker'], text=True).split():
            logs = subprocess.run(['docker', 'logs', container], capture_output=True, text=True, check=True)
            for line in (logs.stdout + logs.stderr).splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get('event') == 'worker_started' and event.get('worker_id') == first['worker_id']:
                    owners.append(container)
                    break
        assert len(owners) == 1, 'could not identify the task owner'
        child_name = f'repopilot-service-{task_id}'
        old_child = json.loads(subprocess.check_output(['docker', 'inspect', child_name]))[0]['Id']
        assert request(path)['status'] == 'RUNNING'
        owner = owners[0]
        subprocess.run(['docker', 'kill', owner], check=True, capture_output=True)
        # The next attempt stages this operator-updated fixture; the original staged
        # workspace stays unchanged. This is recovery plumbing, not a coding benchmark.
        fixture.write_text('def test_crash():\n    assert True\n')
        while time.monotonic() < deadline:
            task = request(path)
            if task['status'] in {'FAILED', 'SUCCEEDED'}:
                break
            time.sleep(.25)
        assert task['status'] == 'SUCCEEDED', task
        assert [attempt['status'] for attempt in task['runs']] == ['ABANDONED', 'SUCCEEDED'], task
        assert subprocess.run(['docker', 'inspect', old_child], capture_output=True).returncode != 0
        assert request(path + '/result')['result']['success']
        return {'task_id': task_id, 'passed': 1,
                'check': 'Compose worker SIGKILL, orphan removal and successful second attempt'}
    finally:
        shutil.rmtree(source)
        if task_id:
            subprocess.run(['docker', 'rm', '-f', f'repopilot-service-{task_id}'], capture_output=True)
        if owner:
            # An explicit docker kill can suppress the restart policy. Restore
            # the test stack's capacity even when an assertion above failed.
            subprocess.run(['docker', 'start', owner], check=True, capture_output=True)


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
