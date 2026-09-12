import os
from alembic import context
from sqlalchemy import create_engine, pool

url = os.environ["REPOPILOT_DATABASE_URL"]
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
url = url.replace("postgresql://", "postgresql+psycopg://", 1)
if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
