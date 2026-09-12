#!/usr/bin/env python3
"""Disposable local PostgreSQL + real HTTP/process benchmark. Scripted only; no paid model calls.
Run: uv run --extra service python scripts/service-stress.py --output docs/SERVICE_STRESS.json
Default executor sleeps; --agent uses the existing scripted AgentLoop and restricted Docker sandbox.
Database operations target only a randomly named disposable container created here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from contextlib import contextmanager
import fcntl
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

import psycopg
from repopilot.service.settings import Settings
from repopilot.service.store import Store
from repopilot.service.worker import Worker


def wait(predicate, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('bounded wait expired')


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def connection_error(exc):
    message = str(exc).lower()
    for phrase in ('cannot assign requested address', 'too many open files', 'too many clients',
                   'server closed the connection unexpectedly', 'connection refused', 'timeout',
                   'resource temporarily unavailable'):
        if phrase in message:
            return phrase
    return type(exc).__name__ + ':' + str(getattr(exc, 'sqlstate', None))


def api(settings, address):
    import uvicorn
    from repopilot.service.api import create_app
    app = create_app(settings)
    original = app.state.store.connect
    @contextmanager
    def connect():
        try:
            with original() as connection:
                yield connection
        except psycopg.Error as exc:
            with (settings.workspace_root.parent/'api-errors.jsonl').open('a') as log:
                log.write(json.dumps({'reason':connection_error(exc), 'time':time.time()})+'\n')
            raise
    app.state.store.connect = connect
    uvicorn.run(app, host='127.0.0.1', port=address, log_level='critical', access_log=False)


def worker(settings, directory, index, gate, duration):
    directory = Path(directory)
    with (directory / f'worker-{index}.jsonl').open('a', buffering=1) as log:
        def record(kind, **fields):
            log.write(json.dumps(dict(kind=kind, time=time.time(), **fields)) + '\n')
        class TimedStore(Store):
            @contextmanager
            def connect(self):
                start = time.perf_counter()
                try:
                    with super().connect() as result:
                        record('connect', ms=(time.perf_counter()-start)*1000)
                        yield result
                except psycopg.Error as exc:
                    record('connect_error', reason=connection_error(exc))
                    raise
            def claim(self, ident):
                start = time.perf_counter()
                result = super().claim(ident)
                record('claim', ms=(time.perf_counter()-start)*1000, found=result is not None)
                return result
        from repopilot.service.executor import AgentExecutor
        adapter = AgentExecutor(settings) if duration is None else None
        def execute(claim):
            task = str(claim.task['id'])
            with (settings.artifact_root / f'witness-{task}').open('a+b') as witness:
                try:
                    fcntl.flock(witness, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    record('overlap', task=task)
                    raise RuntimeError('simultaneous execution')
                record('start', task=task, run=str(claim.run['id']))
                (directory / f'entered-{task}').touch()
                while (directory / f'hold-{task}').exists():
                    time.sleep(.01)
                result = adapter(claim) if adapter else None
                if adapter is None:
                    time.sleep(duration)
                record('end', task=task, run=str(claim.run['id']))
            return result if adapter else {'success': True, 'stop_reason': 'scripted_stress'}
        if adapter:
            execute.cleanup = adapter.cleanup
        instance = Worker(settings, executor=execute, worker_id=f'stress-{uuid4()}')
        instance.store.close()
        instance.store = TimedStore(settings, pool_size=1)
        signal.signal(signal.SIGTERM, lambda *_: instance.request_stop())
        signal.signal(signal.SIGINT, lambda *_: instance.request_stop())
        (directory / f'ready-{index}').touch()
        gate.wait()
        instance.run()


def quantiles(values):
    values = sorted(values)
    def q(p):
        return round(values[max(0, __import__('math').ceil(len(values)*p)-1)], 3) if values else None
    return {'n': len(values), 'p50': q(.5), 'p95': q(.95), 'max': q(1)}


class Experiment:
    def __init__(self, root, url):
        self.ctx = mp.get_context('spawn')
        self.settings = Settings(url, root/'repos', root/'artifacts', 'local-stress-token-only',
                                 lease_seconds=3, hard_timeout_seconds=60, scripted=True)
        (self.settings.workspace_root/'example').mkdir(parents=True)
        (self.settings.workspace_root/'example'/'test_ok.py').write_text('def test_ok():\n    assert True\n')
        self.root = root
        self.store = Store(self.settings)
        self.address = port()
        self.children = []
        self.gates = []
        self.start_api()

    def start_api(self):
        self.server = self.ctx.Process(target=api, args=(self.settings, self.address))
        self.server.start()
        self.children.append(self.server)
        wait(lambda: self.request('/readyz')[0] == 200)

    def request(self, path, payload=None, key=None):
        headers = {'Authorization': 'Bearer '+self.settings.api_token, 'Content-Type': 'application/json'}
        if key:
            headers['Idempotency-Key'] = key
        req = urllib.request.Request(f'http://127.0.0.1:{self.address}'+path,
                                     data=json.dumps(payload).encode() if payload else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)
        except (OSError, TimeoutError):
            return 0, {}

    def submit(self, key):
        start = time.perf_counter()
        status, body = self.request('/v1/tasks', {'repository':'example', 'issue':'scripted stress', 'profile':'default'}, key)
        return {'status':status, 'id':body.get('id'), 'ms':(time.perf_counter()-start)*1000}

    def reset(self):
        with self.store.connect() as db:
            db.execute('TRUNCATE tasks,runs CASCADE')

    def workers(self, count, directory, duration=.1):
        directory.mkdir(parents=True, exist_ok=True)
        gate = self.ctx.Event()
        self.gates.append(gate)
        processes = [self.ctx.Process(target=worker, args=(self.settings, directory, i, gate, duration)) for i in range(count)]
        for process in processes:
            process.start()
            self.children.append(process)
        wait(lambda: len(list(directory.glob('ready-*')))==count)
        gate.set()
        return processes

    def stop(self, processes, *, signal_stop=True):
        for process in processes:
            if signal_stop and process.is_alive():
                process.terminate()
        for process in processes:
            process.join(65)
            if process.is_alive():
                process.kill()
                process.join()
                raise AssertionError('graceful drain exceeded watchdog')
            assert process.exitcode == 0, process.exitcode

    def counts(self):
        with self.store.connect() as db:
            return db.execute("SELECT count(*) AS total, count(*) FILTER(WHERE status IN ('SUCCEEDED','FAILED')) AS terminal FROM tasks").fetchone()

    def scaling(self, count, submissions, repeat, n, duration=.1):
        self.reset()
        directory = self.root/f'w{count}-c{submissions}-r{repeat}'
        processes = self.workers(count, directory, duration)
        samples = []
        sample_errors = []
        sampling_done = threading.Event()
        def sample():
            while not sampling_done.is_set():
                try:
                    with self.store.connect() as db:
                        db.autocommit = True
                        while not sampling_done.is_set():
                            samples.append(db.execute("""SELECT count(*) AS connections,
                                count(*) FILTER(WHERE wait_event_type='Lock') AS lock_waiters,
                                (SELECT count(*) FROM tasks WHERE status IN ('SUCCEEDED','FAILED')) AS terminal,
                                (SELECT count(*) FROM tasks) AS total
                                FROM pg_stat_activity WHERE datname=current_database()""").fetchone())
                            samples[-1]['observed_at'] = time.monotonic()
                            sampling_done.wait(.1)
                except psycopg.Error as exc:
                    sample_errors.append(connection_error(exc))
                    sampling_done.wait(.1)
        sampler = threading.Thread(target=sample, daemon=True)
        self.sampling_done = sampling_done
        self.sampler = sampler
        sampler.start()
        start = time.perf_counter()
        # Every logical request has two concurrent submissions with the same key.
        keys = [str(uuid4()) for _ in range(n)]
        with ThreadPoolExecutor(max_workers=submissions) as pool:
            responses = list(pool.map(self.submit, keys+keys))
        self.last_admissions = dict(workers=count, submitters=submissions, responses=responses)
        submitted_at = time.monotonic()
        wait(lambda: samples and samples[-1].get('observed_at',0)>submitted_at
             and samples[-1]['terminal']==samples[-1]['total'], 120)
        elapsed = time.perf_counter()-start
        self.stop(processes)
        sampling_done.set()
        sampler.join(10)
        assert not sampler.is_alive()
        final_read_errors = []
        def readable():
            try:
                with self.store.connect():
                    return True
            except psycopg.Error as exc:
                final_read_errors.append(connection_error(exc))
                return False
        wait(readable,60)
        with self.store.connect() as db:
            rows = db.execute("""SELECT t.status, extract(epoch FROM (r.started_at-t.created_at))*1000 AS queue,
                extract(epoch FROM (t.completed_at-t.created_at))*1000 AS total,
                extract(epoch FROM (r.completed_at-r.started_at))*1000 AS run
                FROM tasks t JOIN runs r ON r.id=t.latest_run_id""").fetchall()
            attempts = db.execute('SELECT count(*) AS n FROM runs').fetchone()['n']
        events = [json.loads(line) for file in directory.glob('*.jsonl') for line in file.read_text().splitlines()]
        active = peak = 0
        for e in sorted((e for e in events if e['kind'] in ('start','end')), key=lambda e:e['time']):
            active += 1 if e['kind']=='start' else -1
            peak = max(peak,active)
        result = dict(peak_parallel_executions=peak, workers=count, submitters=submissions, repeat=repeat, logical_tasks=n, admitted_tasks=len(rows), unadmitted_tasks=n-len(rows), executor_seconds=duration,
                      sampling_errors=sample_errors, peak_sampled_db_connections=max(v['connections'] for v in samples),
                      peak_sampled_lock_waiters=max(v['lock_waiters'] for v in samples),
                      elapsed_seconds=round(elapsed,3), throughput_tasks_per_second=round(len(rows)/elapsed,3),
                      submission_ms=quantiles([r['ms'] for r in responses]),
                      queue_ms=quantiles([float(r['queue']) for r in rows]),
                      end_to_end_ms=quantiles([float(r['total']) for r in rows]),
                      run_ms=quantiles([float(r['run']) for r in rows]),
                      claim_ms=quantiles([e['ms'] for e in events if e['kind']=='claim' and e['found']]),
                      worker_connect_ms=quantiles([e['ms'] for e in events if e['kind']=='connect']),
                      duplicate_executions=max(0,sum(e['kind']=='start' for e in events)-len(rows)),
                      final_read_errors=final_read_errors, worker_connection_errors=dict(Counter(e['reason'] for e in events if e['kind']=='connect_error')),
                      simultaneous_duplicates=sum(e['kind']=='overlap' for e in events), attempts=attempts,
                      failures=sum(r['status']!='SUCCEEDED' for r in rows),
                      admission_failures=sum(r['status'] not in (200,202) for r in responses),
                      created=sum(r['status']==202 for r in responses), deduplicated=sum(r['status']==200 for r in responses))
        if result['admission_failures']==0:
            assert len(rows)==n and result['created']==n and result['deduplicated']==n
        self.last_result = result
        assert result['simultaneous_duplicates']==0
        print(json.dumps(result), flush=True)
        return result

    def interruptions(self, container):
        outcomes = []
        self.interruption_results = outcomes
        for scenario in ('graceful', 'kill', 'pause', 'db_restart', 'api_restart', 'row_contention'):
            self.current_scenario = scenario
            self.reset()
            directory = self.root/scenario
            directory.mkdir()
            task = self.submit(str(uuid4()))['id']
            hold = directory/f'hold-{task}'
            hold.touch()
            if scenario == 'row_contention':
                blocker = self.store.connect()
                blocker.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (task,))
                other = self.submit(str(uuid4()))['id']
            processes = self.workers(1, directory, .1)
            if scenario == 'row_contention':
                wait(lambda: (directory/f'entered-{other}').exists())
                assert not (directory/f'entered-{task}').exists()
                blocker.rollback()
                blocker.close()
            wait(lambda: (directory/f'entered-{task}').exists())
            start = time.perf_counter()
            if scenario == 'kill':
                processes[0].kill()
                processes[0].join()
                processes = self.workers(2, directory/'replacement', .1)
            elif scenario == 'pause':
                os.kill(processes[0].pid, signal.SIGSTOP)
                time.sleep(4)
                processes += self.workers(2, directory/'replacement', .1)
                time.sleep(1.2)  # competitor gets at least one full poll while original holds flock
                assert len(self.store.get(task)['runs'])==1
                os.kill(processes[0].pid, signal.SIGCONT)
            elif scenario == 'graceful':
                pending = self.submit(str(uuid4()))['id']
                processes[0].terminate()
                # Signal delivered during an in-flight executor must drain it.
                time.sleep(.2)
                assert processes[0].is_alive()
            elif scenario == 'db_restart':
                subprocess.run(['docker','stop','-t','0',container], check=True, stdout=subprocess.DEVNULL)
                time.sleep(4)  # exceeds actual lease; executor remains gated with flock
                subprocess.run(['docker','start',container], check=True, stdout=subprocess.DEVNULL)
                wait(lambda: self.request('/readyz')[0]==200)
                processes += self.workers(2, directory/'replacement', .1)
                time.sleep(1.2)
                assert self.store.get(task)['runs'][-1]['attempt']==1
            elif scenario == 'api_restart':
                self.server.kill()
                self.server.join()
                self.start_api()
                assert self.store.get(task)['status']=='RUNNING'
            hold.unlink()
            wait(lambda: self.store.get(task)['status']=='SUCCEEDED')
            self.stop(processes, signal_stop=scenario != 'graceful')
            row = self.store.get(task)
            if scenario == 'graceful':
                assert self.store.get(pending)['status']=='QUEUED'
                replacement = self.workers(1, directory/'replacement', .1)
                wait(lambda: self.store.get(pending)['status']=='SUCCEEDED')
                self.stop(replacement)
            expected = 2 if scenario in ('kill','pause','db_restart') else 1
            assert len(row['runs'])==expected, row
            events = [json.loads(line) for file in directory.rglob('*.jsonl') for line in file.read_text().splitlines()]
            assert not any(e['kind']=='overlap' for e in events)
            outcomes.append(dict(scenario=scenario, recovery_seconds=round(time.perf_counter()-start,3),
                                 attempts=len(row['runs']), statuses=[r['status'] for r in row['runs']], simultaneous_duplicates=0))
        return outcomes

    def plans(self):
        self.reset()
        with self.store.connect() as db:
            db.execute("""INSERT INTO tasks(id,request_id,status,payload,provider,model,fingerprint,completed_at)
                SELECT gen_random_uuid(),gen_random_uuid(),'SUCCEEDED','{}','scripted','scripted','history',clock_timestamp()
                FROM generate_series(1,100000)""")
            db.execute('ANALYZE tasks')
        query = """SELECT t.* FROM tasks t LEFT JOIN runs r ON r.id=t.latest_run_id
              WHERE (t.status='QUEUED' OR (t.status='RUNNING' AND r.lease_expires_at < clock_timestamp()))
              AND NOT(t.id=ANY('{}'::uuid[])) ORDER BY t.created_at,t.id LIMIT 1 FOR UPDATE OF t SKIP LOCKED"""
        with self.store.connect() as db:
            result = {}
            for name, sql in [('original',query),('explicit_active_predicate',query.replace("WHERE (", "WHERE t.status IN ('QUEUED','RUNNING') AND (",1))]:
                result[name] = db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql).fetchone()['QUERY PLAN']
            # Valid synthetic active history probes query cost independently of workers.
            db.execute("""INSERT INTO tasks(id,request_id,payload,provider,model,fingerprint)
                SELECT gen_random_uuid(),gen_random_uuid(),'{}','scripted','scripted','active-history'
                FROM generate_series(1,10000)""")
            db.execute("""INSERT INTO runs(id,task_id,attempt,status,worker_id,trace_id,lease_expires_at)
                SELECT gen_random_uuid(),id,1,'RUNNING','synthetic-plan','synthetic-plan',clock_timestamp()+interval '1 hour'
                FROM tasks WHERE fingerprint='active-history'""")
            db.execute("""UPDATE tasks t SET status='RUNNING',latest_run_id=r.id FROM runs r WHERE r.task_id=t.id""")
            db.execute('ANALYZE tasks')
            db.execute('ANALYZE runs')
            result['active_10000_no_expired'] = db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()['QUERY PLAN']
            db.execute("UPDATE runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=(SELECT id FROM runs LIMIT 1)")
            result['active_10000_one_expired'] = db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()['QUERY PLAN']
            db.execute("""INSERT INTO tasks(id,request_id,payload,provider,model,fingerprint)
                VALUES (gen_random_uuid(),gen_random_uuid(),'{}','scripted','scripted','queued-tail')""")
            db.execute("UPDATE runs SET lease_expires_at=clock_timestamp()+interval '1 hour'")
            result['active_10000_queued_tail'] = db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()['QUERY PLAN']
            from repopilot.service.store import QUEUED_QUERY, RECOVERY_QUERY
            for label, sql in [('current_queued',QUEUED_QUERY),('current_recovery',RECOVERY_QUERY)]:
                result[label] = db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql, ([],)).fetchone()['QUERY PLAN']
            result['constraints'] = db.execute("""SELECT conname, pg_get_constraintdef(oid) AS definition
                FROM pg_constraint WHERE conrelid IN ('tasks'::regclass,'runs'::regclass) ORDER BY conname""").fetchall()
            result['indexes'] = db.execute("SELECT indexname,indexdef FROM pg_indexes WHERE tablename IN ('tasks','runs') ORDER BY indexname").fetchall()
            result['database'] = db.execute("SELECT version(), current_setting('max_connections') AS max_connections, current_setting('shared_buffers') AS shared_buffers").fetchone()
            result['stats'] = db.execute('SELECT xact_commit,xact_rollback,deadlocks,temp_bytes FROM pg_stat_database WHERE datname=current_database()').fetchone()
        return result

    def close(self):
        if hasattr(self, "sampling_done"):
            self.sampling_done.set()
            self.sampler.join(10)
        for process in self.children:
            if process.is_alive():
                os.kill(process.pid, signal.SIGCONT)
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', default='1:32,2:32,4:32,8:32,16:32,32:32,16:8,16:64')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--agent', action='store_true', help='real scripted AgentLoop + restricted Docker sandbox')
    parser.add_argument('--duration', type=float, default=.1, help='scripted executor seconds per task')
    parser.add_argument('--skip-faults', action='store_true')
    parser.add_argument('--tasks', type=int, default=192)
    args = parser.parse_args()
    name = 'repopilot-stress-'+uuid4().hex[:12]
    address = port()
    experiment = None
    report = dict(started_at=datetime.now(timezone.utc).isoformat(), source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), *Path('src/repopilot/service').glob('*.py')]}, environment={'platform':platform.platform(), 'python':platform.python_version(), 'host_logical_cpus':os.cpu_count(), 'docker_resources':subprocess.check_output(['docker','info','--format','{{.NCPU}} CPUs; {{.MemTotal}} bytes RAM'],text=True).strip()},
                  workload={'executor':('real scripted AgentLoop + restricted Docker; no provider calls' if args.agent else f'injected deterministic {args.duration}s callable; real Worker/Store/HTTP/PostgreSQL; no AgentLoop/model calls'),
                            'poll_seconds':1, 'sampler_interval_seconds':.1, 'quantile_method':'nearest rank', 'throughput_interval':'first HTTP submission through final DB terminal observation; includes duplicate requests and poll delay', 'lease_seconds':3, 'repetitions':args.repeats, 'duplicate_requests_per_task':2, 'worker_startup_excluded':True, 'api_pool_limit':8, 'worker_pool_limit':1, 'worker_connect_ms':'pool acquisition plus connection health check (includes setup when needed)'}, scaling=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(['docker','run','-d','--name',name,'-e','POSTGRES_PASSWORD=stress-local-only','-e','POSTGRES_DB=stress',
                        '-p',f'127.0.0.1:{address}:5432','postgres:16-alpine'], check=True, stdout=subprocess.DEVNULL)
        url = f'postgresql://postgres:stress-local-only@127.0.0.1:{address}/stress'
        def ready():
            try:
                with psycopg.connect(url,connect_timeout=1):
                    return True
            except psycopg.Error:
                return False
        wait(ready)
        subprocess.run(['uv','run','--extra','service','alembic','upgrade','head'], check=True,
                       env={**os.environ, 'REPOPILOT_DATABASE_URL':url}, stdout=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix='repopilot-stress-') as root:
            experiment = Experiment(Path(root), url)
            for repeat in range(1,args.repeats+1):
                for workers, submitters in [tuple(map(int,case.split(':'))) for case in args.cases.split(',') if case]:
                    report['scaling'].append(experiment.scaling(workers,submitters,repeat,args.tasks,None if args.agent else args.duration))
                    args.output.write_text(json.dumps(report,indent=2)+'\n')
            if not args.skip_faults:
                report['interruptions'] = experiment.interruptions(name)
            report['query_plans'] = experiment.plans()
            api_errors = Path(root)/'api-errors.jsonl'
            report['api_connection_errors'] = dict(Counter(json.loads(line)['reason'] for line in api_errors.read_text().splitlines())) if api_errors.exists() else {}
            report['passed'] = all(all(case[key]==0 for key in ('admission_failures','failures','duplicate_executions','simultaneous_duplicates','unadmitted_tasks')) for case in report['scaling'])
            report['verification_completed'] = True
            args.output.write_text(json.dumps(report,indent=2,default=str)+'\n')
    except BaseException as exc:
        report['passed'] = False
        report['error_type'] = type(exc).__name__
        if experiment:
            report['last_admissions'] = getattr(experiment, 'last_admissions', None)
            report['last_result'] = getattr(experiment, 'last_result', None)
            report['interruptions'] = getattr(experiment, 'interruption_results', [])
            report['failed_scenario'] = getattr(experiment, 'current_scenario', None)
        raise
    finally:
        report['database_container_state'] = subprocess.run(['docker','inspect','--format','{{json .State}}',name], capture_output=True,text=True).stdout.strip()
        args.output.with_suffix('.postgres.log').write_text(subprocess.run(['docker','logs',name],capture_output=True,text=True).stderr)
        args.output.write_text(json.dumps(report,indent=2,default=str)+'\n')
        if experiment:
            experiment.close()
        subprocess.run(['docker','rm','-f',name], stdout=subprocess.DEVNULL, check=False)
    print('Evidence:', args.output)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
