"""Persistent tasks and fenced run attempts."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE tasks (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL,
        status text NOT NULL DEFAULT 'QUEUED'
            CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
        payload jsonb NOT NULL,
        provider text NOT NULL,
        model text NOT NULL,
        idempotency_hash text UNIQUE,
        fingerprint text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        completed_at timestamptz,
        latest_run_id uuid,
        CHECK ((status IN ('SUCCEEDED','FAILED')) = (completed_at IS NOT NULL))
    );
    CREATE TABLE runs (
        id uuid PRIMARY KEY,
        task_id uuid NOT NULL REFERENCES tasks(id),
        attempt integer NOT NULL CHECK (attempt > 0),
        status text NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','ABANDONED')),
        worker_id text NOT NULL,
        trace_id text NOT NULL,
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        lease_expires_at timestamptz NOT NULL,
        completed_at timestamptz,
        stop_reason text,
        error_code text,
        artifact_path text,
        result jsonb,
        UNIQUE (task_id, attempt),
        UNIQUE (task_id, id),
        CHECK ((status = 'RUNNING') = (completed_at IS NULL))
    );
    ALTER TABLE tasks ADD CONSTRAINT latest_run_belongs_to_task
        FOREIGN KEY (id, latest_run_id) REFERENCES runs(task_id, id);
    CREATE UNIQUE INDEX one_active_run ON runs(task_id) WHERE status = 'RUNNING';
    CREATE INDEX task_dispatch ON tasks(created_at, id) WHERE status IN ('QUEUED','RUNNING');
    CREATE INDEX expired_runs ON runs(lease_expires_at) WHERE status = 'RUNNING';
    CREATE FUNCTION enforce_task_transition() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status <> NEW.status AND NOT (
        (OLD.status = 'QUEUED' AND NEW.status = 'RUNNING') OR
        (OLD.status = 'RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED'))
      ) THEN RAISE EXCEPTION 'invalid task transition'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER task_transition BEFORE UPDATE ON tasks
      FOR EACH ROW EXECUTE FUNCTION enforce_task_transition();
    CREATE FUNCTION enforce_run_transition() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status <> NEW.status AND NOT (
        OLD.status = 'RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','ABANDONED')
      ) THEN RAISE EXCEPTION 'invalid run transition'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER run_transition BEFORE UPDATE ON runs
      FOR EACH ROW EXECUTE FUNCTION enforce_run_transition();
    """)


def downgrade():
    op.execute("""
    ALTER TABLE tasks DROP CONSTRAINT latest_run_belongs_to_task;
    DROP TABLE runs;
    DROP TABLE tasks;
    DROP FUNCTION enforce_task_transition();
    DROP FUNCTION enforce_run_transition();
    """)
