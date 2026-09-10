"""
Thread-safe progress tracking for long-running background jobs (the DB15
batch lookup and the Claude scoring step), so the frontend can poll a status
endpoint instead of blocking on one long request.

This app is single-user and local, so one ProgressTracker instance per
operation type is enough -- no job IDs needed. A job's worker thread calls
start()/update()/finish()/fail(); an HTTP GET endpoint calls snapshot().
"""

import threading
from typing import Optional


class ProgressTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict = {
            "status": "idle",  # idle | running | done | error
            "completed": 0,
            "total": 0,
            "message": None,
            "result": None,
        }

    def start(self, total: int):
        with self._lock:
            self._state = {
                "status": "running",
                "completed": 0,
                "total": total,
                "message": None,
                "result": None,
            }

    def update(self, completed: int, message: Optional[str] = None):
        with self._lock:
            if self._state["status"] != "running":
                return
            self._state["completed"] = completed
            if message is not None:
                self._state["message"] = message

    def finish(self, result: dict):
        with self._lock:
            self._state["status"] = "done"
            self._state["completed"] = self._state["total"]
            self._state["result"] = result

    def fail(self, message: str):
        with self._lock:
            self._state["status"] = "error"
            self._state["message"] = message

    def is_running(self) -> bool:
        with self._lock:
            return self._state["status"] == "running"

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)
