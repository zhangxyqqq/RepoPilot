from __future__ import annotations

import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from repopilot.sandbox import DockerSandbox


def _set_fixture_owner(image: str, mount: str, uid: int, gid: int) -> None:
    # Trusted fixture preparation, not agent execution. Change ownership only on
    # this disposable mount; retain 0755/0644 permissions and never trust host Git.
    script = """
import os
from pathlib import Path
root = Path('/fixture')
for directory, dirs, files in os.walk(root, followlinks=False):
    for path in [Path(directory), *(Path(directory) / name for name in dirs + files)]:
        os.chown(path, UID, GID, follow_symlinks=False)
""".replace('UID', str(uid)).replace('GID', str(gid))
    subprocess.run([
        'docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--user', '0', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
        '--security-opt', 'no-new-privileges', '--mount', mount,
        '--entrypoint', 'python', image, '-c', script,
    ], check=True, capture_output=True, text=True, timeout=30)


@pytest.mark.docker
def test_hybrid_retrieval_runs_inside_hardened_sandbox_without_worktree_cache(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def rollback_reservations(items):\n    return items\n",
        encoding="utf-8",
    )
    (repository / "distractor.py").write_text("def allocation_report():\n    return []\n", encoding="utf-8")
    sandbox = DockerSandbox(
        repository,
        test_command=("python", "-m", "pytest", "-q"),
        command_timeout_seconds=30,
        issue="undo an allocation",
        retrieval={"strategy": "hybrid", "max_files": 2, "max_chars": 2000},
    )
    sandbox.ensure_image()
    mount = f'type=bind,src={repository},dst=/fixture'
    original = repository.stat()
    try:
        _set_fixture_owner(sandbox.config.image, mount, 10001, 10001)
        with sandbox:
            response = sandbox.invoke("list_files", {})
            inspection = sandbox.invoke("git_diff", {})
            config = json.loads(subprocess.run(
                ['docker', 'inspect', sandbox.container_name], check=True,
                capture_output=True, text=True).stdout)[0]
            assert config['Config']['User'] == '10001:10001'
            assert config['HostConfig']['ReadonlyRootfs'] is True
            assert config['HostConfig']['NetworkMode'] == 'none'
            assert config['HostConfig']['CapDrop'] == ['ALL']
            assert 'no-new-privileges' in config['HostConfig']['SecurityOpt']
    finally:
        # Restore the disposable tree so the host pytest process can remove it.
        _set_fixture_owner(sandbox.config.image, mount, original.st_uid, original.st_gid)

    assert response["ok"] is True
    retrieval = response["result"]["repository_context"]["retrieval"]
    assert retrieval["strategy"] == "hybrid"
    assert retrieval["fallback"] is None
    assert retrieval["cache"]["location"] == "ephemeral_tmpfs"
    assert retrieval["cache"]["persistent_service"] is False
    assert inspection["ok"] is True
    assert inspection["result"]["changed_files"] == []
    assert not any("retrieval-cache" in str(path) for path in repository.rglob("*"))


@pytest.mark.docker
def test_retrieval_fixture_ownership_on_native_linux_volume(tmp_path: Path) -> None:
    """Keep the Linux UID/mode mismatch visible on Docker Desktop too."""
    sandbox = DockerSandbox(tmp_path, test_command=('python', '-m', 'pytest', '-q'),
                            command_timeout_seconds=30)
    sandbox.ensure_image()
    image = sandbox.config.image
    volume = 'repopilot-retrieval-permissions-' + uuid4().hex
    subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
    mount = f'type=volume,src={volume},dst=/fixture'
    try:
        # Emulate pytest's mkdir/write_text with a 022 umask, without broad writes.
        setup = """
from pathlib import Path
root = Path('/fixture')
root.chmod(0o755)
(root / 'module.py').write_text('def rollback_reservations(items):\\n    return items\\n')
(root / 'module.py').chmod(0o644)
"""
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0',
                        '--mount', mount, '--entrypoint', 'python', image, '-c', setup],
                       check=True, capture_output=True, text=True, timeout=30)
        _set_fixture_owner(image, mount, 1001, 1001)
        command = [
            'docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--user', '10001:10001', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m',
            '--tmpfs', '/home/repopilot:rw,nosuid,nodev,size=16m',
            '--env', 'HOME=/home/repopilot',
            '--mount', f'type=volume,src={volume},dst=/workspace',
            '--entrypoint', 'python', image, '-c',
        ]
        before = subprocess.run(command + ["""
import os,sys
from pathlib import Path
sys.path.insert(0, '/opt/repopilot')
import sandbox_runner as runner
assert os.getuid() == 10001 and Path('/workspace').stat().st_uid == 1001
assert not os.access('/workspace', os.W_OK)
runner.init_repo({})
"""], capture_output=True, text=True, timeout=30)
        assert before.returncode != 0 and '/workspace/.git: Permission denied' in before.stderr
        # Apply precisely the same preparation used by the retrieval fixture.
        _set_fixture_owner(image, mount, 10001, 10001)
        after = subprocess.run(command + ["""
import os,sys,stat
from pathlib import Path
sys.path.insert(0, '/opt/repopilot')
import sandbox_runner as runner
assert os.getuid() == 10001 and Path('/workspace').stat().st_uid == 10001
assert stat.S_IMODE(Path('/workspace').stat().st_mode) == 0o755
assert stat.S_IMODE(Path('/workspace/module.py').stat().st_mode) == 0o644
assert runner.init_repo({})['initialized']
result = runner.list_files({'_issue': 'undo an allocation',
    '_retrieval': {'strategy': 'hybrid', 'max_files': 2, 'max_chars': 2000}})
retrieval = result['repository_context']['retrieval']
assert retrieval['strategy'] == 'hybrid' and retrieval['fallback'] is None
assert retrieval['cache']['location'] == 'ephemeral_tmpfs'
assert retrieval['cache']['persistent_service'] is False
assert runner.git_diff({})['changed_files'] == []
assert not any('retrieval-cache' in str(p) for p in Path('/workspace').rglob('*'))
"""], capture_output=True, text=True, timeout=30)
        assert after.returncode == 0, after.stdout + after.stderr
    finally:
        subprocess.run(['docker', 'volume', 'rm', volume], check=True, capture_output=True)
