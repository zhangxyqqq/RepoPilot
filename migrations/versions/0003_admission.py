"""Keep serialized admission inside PostgreSQL instead of holding a lock over RPCs."""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION admit_task(p_id uuid,p_request uuid,p_payload jsonb,p_provider text,
        p_model text,p_key text,p_fingerprint text,p_limit integer) RETURNS SETOF tasks
        LANGUAGE plpgsql VOLATILE AS $$
    DECLARE found_task tasks;
    BEGIN
        IF p_limit < 1 THEN RAISE EXCEPTION 'invalid admission limit'; END IF;
        IF p_key IS NOT NULL THEN
            SELECT * INTO found_task FROM tasks WHERE idempotency_hash=p_key;
            IF FOUND THEN RETURN NEXT found_task; RETURN; END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(742019,1);
        -- VOLATILE PL/pgSQL commands use fresh READ COMMITTED snapshots after
        -- waiting for the mutex; don't collapse this into a single snapshot CTE.
        IF p_key IS NOT NULL THEN
            SELECT * INTO found_task FROM tasks WHERE idempotency_hash=p_key;
            IF FOUND THEN RETURN NEXT found_task; RETURN; END IF;
        END IF;
        IF (SELECT count(*) FROM tasks WHERE status IN ('QUEUED','RUNNING')) >= p_limit THEN
            RAISE EXCEPTION 'admission full' USING ERRCODE='RP001';
        END IF;
        INSERT INTO tasks(id,request_id,payload,provider,model,idempotency_hash,fingerprint)
            VALUES(p_id,p_request,p_payload,p_provider,p_model,p_key,p_fingerprint)
            ON CONFLICT(idempotency_hash) DO NOTHING RETURNING * INTO found_task;
        IF NOT FOUND THEN
            SELECT * INTO found_task FROM tasks WHERE idempotency_hash=p_key;
        END IF;
        RETURN NEXT found_task;
    END $$;
    """)


def downgrade():
    op.execute('DROP FUNCTION admit_task(uuid,uuid,jsonb,text,text,text,text,integer)')
