"""Exercise actual API/PostgreSQL restarts on the local scripted Compose stack."""
import json
from http.client import HTTPException
import subprocess
import time
from urllib.error import URLError
from uuid import uuid4
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

spec = spec_from_file_location('smoke', Path(__file__).with_name('service-smoke.py'))
smoke = module_from_spec(spec)
spec.loader.exec_module(smoke)
request = smoke.request
compose = ['docker', 'compose', '--env-file', '.service.env']


def ready():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            assert request('/readyz')['execution_mode'] == 'scripted', 'API is not in scripted mode'
            return
        except (URLError, HTTPException, ConnectionError):
            time.sleep(.5)
    raise AssertionError('service did not become ready after restart')


ready()
subprocess.run([*compose, 'stop', 'worker'], check=True)
try:
    key = str(uuid4())
    payload = {'repository': 'example', 'issue': 'Verify durability across real process restarts'}
    queued = request('/v1/tasks', body=payload, key=key)
    path = f"/v1/tasks/{queued['id']}"
    assert queued['status'] == 'QUEUED'
    subprocess.run([*compose, 'restart', 'postgres', 'api'], check=True)
    ready()
    assert request(path)['status'] == 'QUEUED'
    assert request('/v1/tasks', body=payload, key=key)['id'] == queued['id']
finally:
    subprocess.run([*compose, 'start', 'worker'], check=True)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    state = request(path)
    if state['status'] in {'SUCCEEDED', 'FAILED'}:
        break
    time.sleep(.25)
assert state['status'] == 'SUCCEEDED', state
before = request(path + '/result')
subprocess.run([*compose, 'restart', 'postgres', 'api'], check=True)
ready()
assert request(path + '/result') == before
assert request(path + '/trace')['runs'][0]['trace_id']
print(json.dumps({'task_id': queued['id'], 'checks': [
    'queued task survives database and API restart', 'idempotency survives restart',
    'workers execute persisted task', 'terminal result survives second restart'], 'passed': 4}, indent=2))
