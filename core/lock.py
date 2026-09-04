"""
core/lock.py
File-based concurrency lock — prevents two operators from running
the same deployment phase against the same environment simultaneously.

Design:
  - Lock file: state/<env>-<phase>.lock
  - Contains: operator, PID, hostname, started_at
  - Lock is acquired at phase start, released at phase end
  - Stale locks (> 4h) are automatically broken with a warning
  - Context manager interface — always released even on exception

Usage:
    from core.lock import Phaselock

    with PhaseLock(env="prod-a", phase="deploy") as lock:
        # deployment code here
        pass  # lock released automatically

If lock cannot be acquired (another process holds it), raises LockConflict.
"""
from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operator() -> str:
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


STALE_AFTER_HOURS = 4


class LockConflict(Exception):
    """Raised when another process holds the lock for this env/phase."""
    pass


class PhaseLock:
    """
    File-based advisory lock for a migration phase.
    Advisory — does not prevent direct file manipulation, but prevents
    tool-level concurrent runs which is the practical requirement.
    """

    def __init__(self, env: str, phase: str, state_dir: str = "state"):
        self._path     = Path(state_dir) / f"{env}-{phase}.lock"
        self._env      = env
        self._phase    = phase
        self._acquired = False
        self._path.parent.mkdir(exist_ok=True)

    def acquire(self) -> None:
        """Acquire the lock. Raises LockConflict if already held by another process."""
        if self._path.exists():
            existing = self._read_lock()
            if existing and not self._is_stale(existing):
                raise LockConflict(
                    f"Environment '{self._env}' phase '{self._phase}' is already running.\n"
                    f"  Operator : {existing.get('operator', '?')}\n"
                    f"  PID      : {existing.get('pid', '?')}\n"
                    f"  Host     : {existing.get('hostname', '?')}\n"
                    f"  Started  : {existing.get('started_at', '?')}\n\n"
                    f"If this is stale (process died), delete: {self._path}"
                )
            elif existing and self._is_stale(existing):
                print(
                    f"\033[33m  ⚠  Breaking stale lock for '{self._env}/{self._phase}' "
                    f"(held by {existing.get('operator','?')} since {existing.get('started_at','?')})\033[0m"
                )

        self._write_lock()
        self._acquired = True

    def release(self) -> None:
        """Release the lock."""
        if self._acquired and self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass
        self._acquired = False

    def _write_lock(self) -> None:
        lock_data = {
            "env":        self._env,
            "phase":      self._phase,
            "operator":   _operator(),
            "pid":        os.getpid(),
            "hostname":   socket.gethostname(),
            "started_at": _now(),
        }
        self._path.write_text(json.dumps(lock_data, indent=2))

    def _read_lock(self) -> Optional[dict]:
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return None

    def _is_stale(self, lock_data: dict) -> bool:
        started = lock_data.get("started_at", "")
        if not started:
            return True
        try:
            started_dt = datetime.fromisoformat(started)
            age = datetime.now(timezone.utc) - started_dt
            return age > timedelta(hours=STALE_AFTER_HOURS)
        except Exception:
            return True

    def __enter__(self) -> "PhaseLock":
        self.acquire()
        return self

    def __exit__(self, *_) -> None:
        self.release()


@contextmanager
def phase_lock(env: str, phase: str, state_dir: str = "state"):
    """Convenience context manager."""
    lock = PhaseLock(env, phase, state_dir)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
