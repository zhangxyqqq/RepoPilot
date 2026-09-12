"""Exercise native Linux ownership even when the host uses Docker Desktop."""
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from repopilot.sandbox import DockerSandbox


@pytest.mark.docker
@pytest.mark.parametrize('owner_uid', [1001, 0], ids=['host-runner', 'compose-controller'])
def test_git_baseline_with_foreign_owned_staging(tmp_path: Path, owner_uid: int):
    sandbox = DockerSandbox(tmp_path, test_command=("python", "-m", "pytest", "-q"), command_timeout_seconds=30)
    sandbox.ensure_image()
    image = sandbox.config.image
    volume = 'repopilot-git-owner-' + uuid4().hex
    subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
    try:
        # A Docker volume preserves Linux ownership; a macOS bind mount does not.
        # Only this fixture initializer runs as root, never the tested agent.
        setup = '''
import os
from pathlib import Path
for name in ('workspace', 'unrelated'):
    path = Path('/fixture') / name
    path.mkdir()
    os.chown(path, OWNER, OWNER)
    path.chmod(0o777)
    (path / 'module.py').write_text('VALUE = 1\\n')
    (path / 'module.py').chmod(0o666)
'''.replace('OWNER', str(owner_uid))
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0',
                        '--mount', f'type=volume,src={volume},dst=/fixture',
                        '--entrypoint', 'python', image, '-c', setup],
                       check=True, capture_output=True, text=True, timeout=30)
        verify = '''
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, '/opt/repopilot')
import sandbox_runner as runner
assert os.getuid() == 10001
assert Path('/fixture/workspace').stat().st_uid == OWNER
assert not (runner.WORKSPACE / '.git').exists()
assert runner.init_repo({}) == {'initialized': True}
assert (runner.WORKSPACE / '.git').is_dir()
assert runner.git_diff({})['changed_files'] == []
assert subprocess.run(runner.git_command('rev-list', '--count', 'HEAD'),
    cwd=runner.WORKSPACE, capture_output=True, text=True, check=True).stdout.strip() == '1'
# Both Git-format patch validation/application and baseline-relative diff must work.
patch = 'diff --git a/module.py b/module.py\\n--- a/module.py\\n+++ b/module.py\\n@@ -1 +1 @@\\n-VALUE = 1\\n+VALUE = 2\\n'
runner.apply_patch({'patch': patch})
assert runner.git_diff({})['changed_files'] == ['module.py']
# The exact workspace exception must not trust another foreign-owned repository.
subprocess.run(['git', 'init', '-q', '-b', 'main', '/fixture/unrelated'], check=True)
untrusted = subprocess.run(runner.git_command('-C', '/fixture/unrelated', 'status'),
    capture_output=True, text=True)
assert untrusted.returncode != 0 and 'dubious ownership' in untrusted.stderr
assert subprocess.run(['git', 'config', '--global', '--get-all', 'safe.directory'],
    capture_output=True).returncode == 1
print(json.dumps({'owner_uid': OWNER, 'agent_uid': os.getuid(), 'baseline': True,
    'patch_and_diff': True, 'unrelated_repository_rejected': True}))
'''.replace('OWNER', str(owner_uid))
        completed = subprocess.run([
            'docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', '10001:10001', '--tmpfs', '/home/repopilot:rw,nosuid,nodev,size=16m',
            '--env', 'HOME=/home/repopilot',
            '--env', 'REPOPILOT_WORKSPACE=/fixture/workspace',
            '--mount', f'type=volume,src={volume},dst=/fixture',
            '--entrypoint', 'python', image, '-c', verify,
        ], capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert json.loads(completed.stdout)['owner_uid'] == owner_uid
    finally:
        subprocess.run(['docker', 'volume', 'rm', volume], check=True, capture_output=True)
