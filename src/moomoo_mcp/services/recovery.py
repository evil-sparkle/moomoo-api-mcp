"""Shared recovery worker lifecycle and execution health presentation."""

import logging
import threading
from collections.abc import Collection


class RecoveryWorker:
    """Run read-only recovery until stopped, waking promptly for new evidence."""

    def __init__(self, name: str, logger: logging.Logger):
        self._name = name
        self._logger = logger
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True
            )
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.run_once()
            except Exception:
                self._logger.exception("%s remains pending", self._name)
            self._wake.wait(timeout=1)

    def run_once(self) -> None:
        raise NotImplementedError


def pending_outcome_health(health: dict, pending: Collection[str]) -> dict:
    """Include observed outcomes that could not be committed to the journal."""
    if pending:
        health["state"] = "JOURNAL_BLOCKED"
        health["blocking_reasons"] = sorted(
            set(health["blocking_reasons"]) | {"OUTCOME_NOT_STORED"}
        )
        health["blocking"] = max(health["blocking"], len(pending))
        health["pending_operation_ids"] = sorted(
            set(health["pending_operation_ids"]) | set(pending)
        )
    return health
