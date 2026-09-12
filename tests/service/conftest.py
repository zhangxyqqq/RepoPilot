import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("psycopg", reason="install the service extra")
pytest.importorskip("fastapi", reason="install the service extra")

from repopilot.service.settings import Settings
from repopilot.service.store import Store


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("REPOPILOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("REPOPILOT_TEST_DATABASE_URL must name a dedicated disposable PostgreSQL database")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={**os.environ, "REPOPILOT_DATABASE_URL": url}, check=True)
    return url


@pytest.fixture
def settings(database_url, tmp_path):
    root = tmp_path / "repos"
    (root / "example").mkdir(parents=True)
    (root / "example" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    value = Settings(database_url, root, tmp_path / "artifacts", "test-control-token", lease_seconds=3, scripted=True)
    with Store(value).connect() as db:
        db.execute("TRUNCATE tasks, runs, service_workers")
    return value
