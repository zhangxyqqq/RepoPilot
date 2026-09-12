from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from repopilot.service.api import create_app
from repopilot.service.artifacts import LocalArtifacts
from repopilot.service.store import Store, AdmissionFull
from repopilot.service.worker import Worker

pytestmark = pytest.mark.postgres
PAYLOAD = {'repository':'example','issue':'depth check','profile':'default'}


def test_concurrent_admission_bound_and_replay(settings):
    bounded = replace(settings,max_inflight=2)
    barrier = threading.Barrier(8)
    def submit(i):
        barrier.wait(timeout=10)
        try:
            return Store(bounded).submit(PAYLOAD,f'key-{i}',uuid4())[0]
        except AdmissionFull:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(submit,range(8)) if r]
    assert len(rows)==2
    store = Store(bounded)
    row = rows[0]
    # Same fingerprint/key replays even while the global capacity is exhausted.
    key = next(f'key-{i}' for i in range(8) if __import__('hashlib').sha256(f'key-{i}'.encode()).hexdigest()==row['idempotency_hash'])
    assert store.submit(PAYLOAD,key,uuid4())[0]['id']==row['id']
    worker = Worker(bounded,lambda claim:{'success':True})
    assert worker.run_once()
    assert store.submit(PAYLOAD,'new-key',uuid4())[1]


def test_overload_contract_metrics_and_auth(settings):
    bounded = replace(settings,max_inflight=1)
    with TestClient(create_app(bounded)) as api:
        assert api.get('/metrics').status_code==401
        api.headers['Authorization']='Bearer '+settings.api_token
        first = api.post('/v1/tasks',json=PAYLOAD,headers={'Idempotency-Key':'same'})
        rejected = api.post('/v1/tasks',json=PAYLOAD,headers={'Idempotency-Key':'other'})
        assert rejected.status_code==429 and rejected.headers['Retry-After']=='1'
        assert rejected.json()['code']=='admission_full'
        assert rejected.json()['request_id']==rejected.headers['X-Request-ID']
        assert api.post('/v1/tasks',json=PAYLOAD,headers={'Idempotency-Key':'same'}).status_code==200
        assert api.post('/v1/tasks',json={**PAYLOAD,'issue':'different'},headers={'Idempotency-Key':'same'}).status_code==409
        worker = Worker(bounded,lambda claim:{'success':True})
        worker.run_once()
        metrics = api.get('/metrics').text
        assert 'repopilot_tasks{status="SUCCEEDED"} 1.0' in metrics
        assert 'repopilot_workers{state="IDLE"} 1.0' in metrics
        assert 'repopilot_admission_total{outcome="rejected"} 1' in metrics
        assert first.json()['id'] not in metrics and settings.api_token not in metrics
        assert 'repopilot_claim_transactions_count 1.0' in metrics
        spec = api.get('/openapi.json').json()
        assert spec['components']['securitySchemes']['HTTPBearer']['scheme']=='bearer'
        assert '429' in spec['paths']['/v1/tasks']['post']['responses']


def test_worker_stop_before_claim_and_during_selection(settings,monkeypatch):
    store = Store(settings)
    task = store.submit(PAYLOAD,None,uuid4())[0]
    calls = []
    worker = Worker(settings,lambda claim:calls.append(claim))
    worker.request_stop()
    assert not worker.run_once() and store.get(task['id'])['status']=='QUEUED'
    worker = Worker(settings,lambda claim:calls.append(claim))
    original = worker.store.connect
    from contextlib import contextmanager
    @contextmanager
    def stop_during_transaction():
        with original() as db:
            worker.request_stop()
            yield db
    monkeypatch.setattr(worker.store,'connect',stop_during_transaction)
    assert not worker.run_once()
    assert calls==[] and store.get(task['id'])['status']=='QUEUED'


def test_worker_registry_expiry_and_database_constraints(settings):
    store = Store(settings)
    assert store.claim('idle-worker') is None
    with store.connect() as db:
        db.execute("UPDATE service_workers SET expires_at=clock_timestamp()-interval '1 second'")
    with TestClient(create_app(settings),headers={'Authorization':'Bearer '+settings.api_token}) as api:
        assert 'repopilot_workers{state="EXPIRED"} 1.0' in api.get('/metrics').text
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.connect() as db:
            db.execute("UPDATE service_workers SET claim_count=-1")


def test_artifact_metadata_rejects_symlink_escape(settings,tmp_path):
    run = str(uuid4())
    root = settings.artifact_root/run
    root.mkdir(parents=True)
    outside = tmp_path/'outside.json'
    outside.write_text(json.dumps({'trace_summary':{'secret':'unpublished'}}))
    (root/'run.json').symlink_to(outside)
    with pytest.raises(ValueError):
        LocalArtifacts(settings.artifact_root).metadata(run)


def test_body_bound_and_uniform_errors(settings):
    with TestClient(create_app(settings),headers={'Authorization':'Bearer '+settings.api_token}) as api:
        response = api.post('/v1/tasks',content=b'x'*131073)
        assert response.status_code==413
        assert response.json()['request_id']==response.headers['X-Request-ID']
        for method,path,kwargs,status in [('get','/v1/tasks/not-a-uuid',{},422),('get','/v1/tasks/'+str(uuid4()),{},404),('post','/v1/tasks',{'content':'invalid json'},422)]:
            response = getattr(api,method)(path,**kwargs)
            assert response.status_code==status
            assert set(response.json())=={'code','detail','request_id'}
