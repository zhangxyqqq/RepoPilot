"""Separate queue/expiry indexes and bounded-cardinality worker telemetry."""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE INDEX task_queued ON tasks(created_at,id) WHERE status='QUEUED';
        CREATE INDEX run_recovery ON runs(lease_expires_at,id) WHERE status='RUNNING';
        DROP INDEX expired_runs;
        CREATE TABLE service_workers (
            worker_id text PRIMARY KEY,
            state text NOT NULL CHECK (state IN ('IDLE','RUNNING','DRAINING','STOPPED')),
            expires_at timestamptz NOT NULL,
            claim_count bigint NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
            claim_seconds double precision NOT NULL DEFAULT 0 CHECK (claim_seconds >= 0)
        );
        CREATE INDEX worker_expiry ON service_workers(expires_at);
    """)


def downgrade():
    op.execute("""DROP TABLE service_workers; DROP INDEX run_recovery; DROP INDEX task_queued;
        CREATE INDEX expired_runs ON runs(lease_expires_at) WHERE status='RUNNING';""")
