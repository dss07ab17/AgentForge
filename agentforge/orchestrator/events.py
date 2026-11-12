"""Structured trace of everything that happens in a run (JSON lines)."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

_ICONS = {
    "run.start": "▶", "run.end": "■", "stage": "◆", "task.start": "→", "task.done": "✓",
    "task.retry": "↻", "task.failed": "✗", "task.blocked": "⊘", "task.spawned": "+",
    "gate": "?", "gate.decision": "✔", "guardrail": "⛨", "validation": "≡", "info": "·",
}


class EventLog:
    def __init__(self, path: Path | None, *, verbose: bool = True) -> None:
        self.path = path
        self.verbose = verbose
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self._t0 = time.time()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, kind: str, message: str, **data: Any) -> None:
        evt = {"ts": round(time.time() - self._t0, 3), "kind": kind, "message": message, **data}
        with self._lock:
            self.events.append(evt)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(evt, default=str) + "\n")
            if self.verbose:
                icon = _ICONS.get(kind, "·")
                print(f"[{evt['ts']:7.2f}s] {icon} {message}", file=sys.stderr, flush=True)
