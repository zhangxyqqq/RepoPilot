"""Bounded regression checks discovered while extending local stress verification."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from repopilot.service.api import create_app
from repopilot.service.store import Store
from repopilot.service.worker import Worker

pytestmark = pytest.mark.postgres
PAYLOAD = {'repository': 'example', 'issue': 'scripted stress', 'profile': 'default'}


def test_uncommitted_idempotency_insert_blocks_then_replays(settings):
    """A unique-index waiter plus admission-mutex waiters, observed before release."""
    store = Store(settings)
    task_id = uuid4()
    key = str(uuid4())
    canonical = json.dumps(PAYLOAD, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    with store.connect() as writer, store.connect() as observer:
        observer.autocommit = True
        writer.execute('''INSERT INTO tasks(id,request_id,payload,provider,model,idempotency_hash,fingerprint)
            VALUES (%s,%s,%s,'scripted','scripted-final',%s,%s)''',
            (task_id, uuid4(), Jsonb(PAYLOAD), hashlib.sha256(key.encode()).hexdigest(),
             hashlib.sha256(canonical.encode()).hexdigest()))
        with TestClient(create_app(settings), headers={'Authorization':f'Bearer {settings.api_token}'}) as api:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(api.post, '/v1/tasks', json=PAYLOAD, headers={'Idempotency-Key':key}) for _ in range(4)]
                try:
                    end = time.monotonic()+2
                    while time.monotonic()<end:
                        n = observer.execute("""SELECT count(*) AS n FROM pg_stat_activity
                            WHERE datname=current_database() AND wait_event_type='Lock'
""").fetchone()['n']
                        if n == 4:
                            break
                        time.sleep(.01)
                    assert n == 4, 'all four API requests must reach real PostgreSQL admission/unique-index waits'
                finally:
                    writer.commit()  # release before executor shutdown, even on failed assertion
                responses = [future.result(10) for future in futures]
        assert [response.status_code for response in responses]==[200]*4
        assert {response.json()['id'] for response in responses}=={str(task_id)}
        assert observer.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']==1


def test_create_snapshot_stays_consistent_if_worker_finishes_before_http_response(settings, monkeypatch):
    app = create_app(settings)
    store = app.state.store
    original = store.submit
    def submit_then_finish(*args):
        task, created = original(*args)
        Worker(settings, lambda claim: {'success':True, 'stop_reason':'scripted'}).run_once()
        return task, created
    monkeypatch.setattr(store, 'submit', submit_then_finish)
    with TestClient(app, headers={'Authorization':f'Bearer {settings.api_token}'}) as api:
        response = api.post('/v1/tasks', json=PAYLOAD)
        assert response.status_code==202
        snapshot = response.json()
        # Creation can return its committed snapshot or a later, internally consistent one.
        assert (snapshot['status']=='QUEUED' and snapshot['latest_run_id'] is None and snapshot['runs']==[]) or (
            snapshot['status']=='SUCCEEDED' and len(snapshot['runs'])==1 and snapshot['runs'][0]['status']=='SUCCEEDED')
        latest = api.get(response.headers['Location']).json()
        assert latest['status']=='SUCCEEDED' and len(latest['runs'])==1


def test_pool_rolls_back_and_returns_idle_connection(settings):
    pooled = Store(settings, pool_size=1)
    try:
        with pooled.connect() as db:
            pid = db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
        with pytest.raises(ValueError):
            with pooled.connect() as db:
                db.execute('''INSERT INTO tasks(id,request_id,payload,provider,model,fingerprint)
                    VALUES (%s,%s,%s,'scripted','scripted-final','rollback')''', (uuid4(),uuid4(),Jsonb(PAYLOAD)))
                raise ValueError('rollback the real transaction')
        with pooled.connect() as db:
            assert db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']==pid
            assert db.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']==0
        with Store(settings).connect() as observer:
            assert observer.execute('SELECT state FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()['state']=='idle'
    finally:
        pooled.close()


def test_pool_discards_dead_backend_before_next_transaction(settings):
    pooled = Store(settings, pool_size=1)
    try:
        with pooled.connect() as db:
            previous = db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
        with Store(settings).connect() as admin:
            admin.execute('SELECT pg_terminate_backend(%s)', (previous,))
        task, created = pooled.submit(PAYLOAD, str(uuid4()), uuid4())
        assert created and pooled.get(task['id'])['status']=='QUEUED'
        with pooled.connect() as db:
            assert db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']!=previous
    finally:
        pooled.close()


def test_pool_bounds_connections_and_reuses_released_slot(settings):
    import threading
    pooled = Store(settings, pool_size=2)
    release = threading.Event()
    entered = threading.Barrier(3)
    def holder():
        with pooled.connect() as db:
            pid = db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
            entered.wait(timeout=10)
            assert release.wait(10)
            return pid
    def waiter():
        with pooled.connect() as db:
            return db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            first = [executor.submit(holder) for _ in range(2)]
            try:
                entered.wait(timeout=10)
                third = executor.submit(waiter)
                deadline = time.monotonic()+2
                while time.monotonic()<deadline and pooled._pool.get_stats().get('requests_waiting',0)!=1:
                    time.sleep(.01)
                assert pooled._pool.get_stats()['requests_waiting']==1
                assert pooled._pool.get_stats()['pool_size']==2
            finally:
                release.set()
            pids = {future.result(10) for future in first}
            assert len(pids)==2 and third.result(10) in pids
    finally:
        pooled.close()


def test_pool_warnings_do_not_echo_driver_secrets(settings, caplog):
    import logging
    pooled = Store(settings, pool_size=1)
    try:
        with caplog.at_level(logging.WARNING):
            logging.getLogger('psycopg.pool').warning('driver exception containing %s', settings.api_token)
        assert settings.api_token not in caplog.text
        assert 'database_pool_warning' in caplog.text
    finally:
        pooled.close()
