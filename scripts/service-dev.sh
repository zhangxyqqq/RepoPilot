#!/bin/sh
# Creates private local configuration and a no-model-token smoke repository.
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .service.env ]; then
  python3 - <<'PY'
import os
from pathlib import Path
import secrets
root = Path.home() / '.local/share/repopilot-service'
repos, artifacts = root / 'repositories', root / 'artifacts'
(repos / 'example').mkdir(parents=True, exist_ok=True)
artifacts.mkdir(parents=True, exist_ok=True)
(repos / 'example/test_example.py').write_text('def test_example():\n    assert 1 + 1 == 2\n')
values = dict(REPOPILOT_WORKSPACE_ROOT=repos.resolve(), REPOPILOT_ARTIFACT_ROOT=artifacts.resolve(),
              REPOPILOT_API_TOKEN=secrets.token_hex(24), REPOPILOT_DB_PASSWORD=secrets.token_hex(24),
              REPOPILOT_SCRIPTED='1')
with open('.service.env', 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
    for key, value in values.items():
        f.write(f'{key}={value}\n')
PY
fi
docker compose --env-file .service.env up --build -d --scale worker=2
