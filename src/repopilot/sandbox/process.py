"""Preserve a service execution lock for the lifetime of trusted Docker helpers.

Normal CLI/evaluation calls retain subprocess.run behavior. Under a service lock,
a small supervisor owns the command timeout even if the worker is SIGKILLed. Both
supervisor and Docker CLI inherit the flock descriptor; it never enters a sandbox.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import subprocess
import sys

_EXECUTION_LOCK: ContextVar[int | None] = ContextVar('repopilot_execution_lock', default=None)


@contextmanager
def execution_lock(fd: int):
    token = _EXECUTION_LOCK.set(fd)
    try:
        yield
    finally:
        _EXECUTION_LOCK.reset(token)


def run_process(command, **kwargs):
    fd = _EXECUTION_LOCK.get()
    if fd is None:
        return subprocess.run(command, **kwargs)
    # The supervisor, not the worker, must enforce this bound: the worker may die.
    timeout = kwargs.pop('timeout', None) or 60
    wrapper = [sys.executable, str(Path(__file__).resolve()), str(fd), str(timeout), *command]
    check = kwargs.pop("check", False)
    completed = subprocess.run(wrapper, pass_fds=(fd,), **kwargs)
    if completed.returncode == 124:
        raise subprocess.TimeoutExpired(command, timeout, completed.stdout, completed.stderr)
    if check and completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout, completed.stderr)
    return subprocess.CompletedProcess(command, completed.returncode, completed.stdout, completed.stderr)


def _main():
    fd, timeout = int(sys.argv[1]), float(sys.argv[2])
    os.fstat(fd)  # Fail closed if the execution descriptor was not inherited.
    try:
        result = subprocess.run(sys.argv[3:], pass_fds=(fd,), timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == '__main__':
    sys.exit(_main())
