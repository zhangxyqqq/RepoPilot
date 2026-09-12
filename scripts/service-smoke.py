"""No paid model calls. Run after service-dev.sh (scripted mode must be enabled)."""
import json
from http.client import HTTPException
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(path, *, body=None, key=None):
    config = dict(line.split('=', 1) for line in Path('.service.env').read_text().splitlines() if line)
    if config.get('REPOPILOT_SCRIPTED') != '1':
        raise SystemExit('smoke requires REPOPILOT_SCRIPTED=1; refusing paid model execution')
    port = os.environ.get('REPOPILOT_API_PORT', '8000')
    headers = {'Authorization': f"Bearer {config['REPOPILOT_API_TOKEN']}"}
    if key:
        headers['Idempotency-Key'] = key
    if body:
        headers['Content-Type'] = 'application/json'
    req = Request(f'http://127.0.0.1:{port}{path}', headers=headers,
                  data=json.dumps(body).encode() if body else None)
    with urlopen(req, timeout=10) as response:
        return json.load(response)


if __name__ == '__main__':
    deadline = time.monotonic() + 90
    while True:
        try:
            assert request('/readyz')['execution_mode'] == 'scripted', 'API is not in scripted mode'
            break
        except (URLError, HTTPError, HTTPException, ConnectionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    payload = {'repository': 'example', 'issue': 'Collect configured tests and diff'}
    key = str(uuid4())
    task = request('/v1/tasks', body=payload, key=key)
    assert request('/v1/tasks', body=payload, key=key)['id'] == task['id']
    path = f"/v1/tasks/{task['id']}"
    while time.monotonic() < deadline:
        task = request(path)
        if task['status'] in {'SUCCEEDED', 'FAILED'}:
            break
        time.sleep(.25)
    assert task['status'] == 'SUCCEEDED', task
    result = request(path + '/result')
    assert result['result']['success'] is True
    trace = request(path + '/trace')
    assert trace['runs'][0]['trace_id']
    print(json.dumps({'task_id': task['id'], 'run_id': result['run_id'], 'status': task['status']}, indent=2))
