"""Asynchronous click analytics.

The redirect hot path only enqueues a ``ClickEvent`` (O(1), no I/O). A
background thread drains the queue and writes in batches. If the queue is full
the event is dropped and counted (load shedding): analytics is allowed to be
slightly lossy under extreme load, redirects are not allowed to slow down.
"""
from __future__ import annotations

import logging
import queue
import threading

from .models import ClickEvent

log = logging.getLogger(__name__)


class ClickRecorder:
    def __init__(self, repo, *, flush_interval: float = 0.5, batch_size: int = 500,
                 max_queue: int = 100_000) -> None:
        self._repo = repo
        self._queue: queue.Queue[ClickEvent] = queue.Queue(maxsize=max_queue)
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.dropped = 0
        self.written = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._flush_lock = threading.Lock()

    def record(self, event: ClickEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def flush(self) -> int:
        """Drain everything currently queued into storage. Returns events written."""
        total = 0
        with self._flush_lock:
            while True:
                batch: list[ClickEvent] = []
                while len(batch) < self.batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                if not batch:
                    break
                try:
                    self._repo.record_clicks(batch)
                    total += len(batch)
                except Exception:  # never let analytics failures take the worker down
                    log.exception("failed to persist %d click events", len(batch))
        self.written += total
        return total

    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="click-recorder", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.flush()

    def _run(self) -> None:
        while not self._stop.wait(self.flush_interval):
            self.flush()
