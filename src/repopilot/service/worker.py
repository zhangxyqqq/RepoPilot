"""One active task per process; scale by starting additional worker processes."""
import os
import signal
import threading
from uuid import uuid4

import psycopg

from repopilot.service.executor import AgentExecutor
from repopilot.service.logging import configure, event
from repopilot.service.settings import Settings
from repopilot.service.store import Store
from repopilot.sandbox.process import execution_lock


class Worker:
    def __init__(self, settings, executor=None, *, worker_id=None):
        self.settings = settings
        self.store = Store(settings, pool_size=1)
        self.executor = executor if executor is not None else AgentExecutor(settings)
        self.worker_id = worker_id or str(uuid4())
        self.stopping = threading.Event()

    def request_stop(self):
        # Signal-safe: no database or cleanup I/O in the signal handler.
        self.store.draining = True
        self.stopping.set()

    def run_once(self):
        if self.stopping.is_set():
            return False
        claim = None
        pulse = None
        done = threading.Event()
        lost = threading.Event()

        def heartbeat():
            while not done.wait(self.settings.lease_seconds / 3):
                try:
                    if not self.store.heartbeat(claim):
                        lost.set()
                        event("lease_lost", **claim.identifiers)
                        return
                except psycopg.Error:
                    # Keep the filesystem lock while the synchronous agent winds down.
                    # A DB partition must not admit a replacement alongside this worker.
                    lost.set()
                    event("heartbeat_failed", **claim.identifiers)
                    return

        def deadline():
            if not done.wait(self.settings.hard_timeout_seconds):
                identifiers = claim.identifiers if claim else {"worker_id": self.worker_id}
                event("worker_hard_timeout", **identifiers)
                # Surviving Docker supervisors retain flock until they complete or
                # time out; replacement cleanup then removes the orphaned sandbox.
                os._exit(70)

        watchdog = threading.Thread(target=deadline, daemon=True)
        watchdog.start()
        try:
            claim = self.store.claim(self.worker_id)
            if claim is None:
                return False
            pulse = threading.Thread(target=heartbeat, daemon=True)
            pulse.start()
            with execution_lock(claim.lock.fileno()):
                return self.execute_claim(claim, lost)
        finally:
            done.set()
            if pulse is not None and pulse.is_alive():
                pulse.join(timeout=10)
            if claim is not None:
                claim.release()

    def execute_claim(self, claim, lost):
        try:
            cleanup = getattr(self.executor, "cleanup", lambda claim: None)
            cleanup(claim)
            # Setup can outlast the lease (including while a worker is paused).
            # Revalidate immediately before authorizing any agent/provider work.
            if lost.is_set() or not self.store.heartbeat(claim):
                event("execution_fenced", **claim.identifiers)
                return True
            if claim.exhausted:
                if not lost.is_set():
                    self.store.finish(claim, error_code="attempts_exhausted")
                return True
            error_code = None
            result = None
            try:
                event("agent_started", **claim.identifiers)
                result = self.executor(claim)
            except Exception:
                # Exception strings can contain credentials or repository contents.
                error_code = "execution_error"
                event("execution_error", **claim.identifiers)
            finally:
                cleanup(claim)
            if not lost.is_set():
                if not self.store.finish(claim, result=result, error_code=error_code):
                    event("completion_fenced", **claim.identifiers)
            return True
        except Exception:
            # Cleanup or DB failure: leave the leased attempt for bounded recovery.
            event("attempt_interrupted", **claim.identifiers)
            return True

    def run(self):
        try:
            while not self.stopping.is_set():
                try:
                    worked = self.run_once()
                except (psycopg.Error, OSError):
                    event("dispatcher_unavailable", worker_id=self.worker_id)
                    worked = False
                if not worked:
                    self.stopping.wait(self.settings.poll_seconds)
        finally:
            try:
                self.store.stopped(self.worker_id)
            except psycopg.Error:
                event('worker_stop_unreported', worker_id=self.worker_id)
            self.store.close()


def main():
    configure()
    worker = Worker(Settings.from_env())
    # Graceful stop drains the current task; Compose's stop grace period exceeds
    # the hard watchdog. SIGKILL recovery is handled by locks, leases and cleanup.
    signal.signal(signal.SIGTERM, lambda *_: worker.request_stop())
    signal.signal(signal.SIGINT, lambda *_: worker.request_stop())
    event("worker_started", worker_id=worker.worker_id)
    worker.run()


if __name__ == "__main__":
    main()
