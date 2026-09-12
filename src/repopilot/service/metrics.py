"""Prometheus text exposition: local API counters and retained database gauges.

Identifiers belong in logs/traces, never labels. Database gauges reflect retained
rows; deleting history can decrease them. API counters reset on process restart.
"""
from collections import Counter
import threading


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = Counter()
        self.http = Counter()
        self.seconds = Counter()

    def event(self, name):
        with self.lock:
            self.events[name] += 1

    def request(self, method, route, status, seconds):
        method = method if method in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'} else 'OTHER'
        with self.lock:
            key = (method,route,str(status))
            self.http[key] += 1
            self.seconds[key] += seconds

    def render(self, store):
        lines = []
        def gauge(name, help_text, values):
            lines.extend([f'# HELP repopilot_{name} {help_text}',f'# TYPE repopilot_{name} gauge'])
            lines.extend(f'repopilot_{name}{labels} {float(value or 0)}' for labels,value in values)
        with store.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            for table in ('tasks','runs'):
                rows = db.execute(f'SELECT status,count(*) n FROM {table} GROUP BY status').fetchall()
                states = ('QUEUED','RUNNING','SUCCEEDED','FAILED') if table=='tasks' else ('RUNNING','SUCCEEDED','FAILED','ABANDONED')
                counts = {r['status']:r['n'] for r in rows}
                gauge(table, 'Retained database rows by lifecycle status.', [(f'{{status="{state}"}}',counts.get(state,0)) for state in states])
            rows = db.execute("""SELECT CASE WHEN state='STOPPED' THEN 'STOPPED'
                WHEN expires_at<=statement_timestamp() THEN 'EXPIRED' ELSE state END state,count(*) n
                FROM service_workers GROUP BY 1""").fetchall()
            counts = {r['state']:r['n'] for r in rows}
            gauge('workers', 'Registered workers; IDLE with valid heartbeat means available.',
                  [(f'{{state="{state}"}}',counts.get(state,0)) for state in ('IDLE','RUNNING','DRAINING','STOPPED','EXPIRED')])
            row = db.execute("""SELECT coalesce(sum(claim_count),0) n,coalesce(sum(claim_seconds),0) seconds
                FROM service_workers""").fetchone()
            gauge('claim_transactions_count', 'Retained successful claim samples.', [('',row['n'])])
            gauge('claim_transactions_seconds_sum', 'DB transaction time through claim bookkeeping; excludes checkout and commit.', [('',row['seconds'])])
            stale = db.execute("SELECT count(*) n FROM runs WHERE status='RUNNING' AND lease_expires_at<statement_timestamp()").fetchone()['n']
            gauge('stale_leases','Expired RUNNING leases, not necessarily safe to execute.', [('',stale)])
            recovered = db.execute("SELECT count(*) n FROM runs WHERE status='ABANDONED' AND error_code='lease_expired'").fetchone()['n']
            gauge('recovered_attempts','Retained abandoned attempts due to lease recovery.', [('',recovered)])
            for name, query in (
                ('task_wait',"SELECT extract(epoch FROM(min(r.started_at)-t.created_at)) seconds FROM tasks t JOIN runs r ON r.task_id=t.id GROUP BY t.id"),
                ('run_duration',"SELECT extract(epoch FROM(completed_at-started_at)) seconds FROM runs WHERE completed_at IS NOT NULL")):
                row = db.execute(f"""SELECT count(*) n,sum(seconds) seconds,
                    percentile_cont(.5) WITHIN GROUP(ORDER BY seconds) p50,
                    percentile_cont(.95) WITHIN GROUP(ORDER BY seconds) p95 FROM ({query}) durations""").fetchone()
                gauge(name+'_seconds', 'Retained duration distribution, seconds.', [(f'{{quantile="{q}"}}',row[key]) for q,key in (('0.5','p50'),('0.95','p95'))])
                gauge(name+'_observations', 'Retained duration observation count.', [('',row['n'])])
                gauge(name+'_seconds_sum', 'Retained duration sum.', [('',row['seconds'])])
        gauge('admission_capacity', 'Configured active-task admission capacity.', [('',store.settings.max_inflight)])
        if store._pool is not None:
            stats = store._pool.get_stats()
            gauge('api_pool', 'API process pool observations and limits.',
                  [(f'{{measure="{key}"}}',stats.get(key,0)) for key in ('pool_max','pool_size','pool_available','requests_waiting')])
        with self.lock:
            lines += ['# HELP repopilot_admission_total API process admission events.', '# TYPE repopilot_admission_total counter']
            for name in ('created','deduplicated','conflict','rejected'):
                lines.append(f'repopilot_admission_total{{outcome="{name}"}} {self.events[name]}')
            lines += ['# HELP repopilot_http_requests_total API process HTTP requests.', '# TYPE repopilot_http_requests_total counter',
                      '# HELP repopilot_http_seconds_sum API process HTTP duration sum.', '# TYPE repopilot_http_seconds_sum counter']
            for (method,route,status), value in sorted(self.http.items()):
                labels = f'{{method="{method}",route="{route}",status="{status}"}}'
                lines += [f'repopilot_http_requests_total{labels} {value}',f'repopilot_http_seconds_sum{labels} {self.seconds[(method,route,status)]}']
        return '\n'.join(lines)+'\n'
