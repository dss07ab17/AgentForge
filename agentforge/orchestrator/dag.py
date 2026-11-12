"""Task graph: dependency validation, readiness, failure propagation, rewiring."""
from __future__ import annotations

from collections import defaultdict, deque

from ..models import Task, TaskStatus


class GraphError(ValueError):
    pass


class TaskGraph:
    def __init__(self, tasks: list[Task] | None = None) -> None:
        self.tasks: dict[str, Task] = {}
        for t in tasks or []:
            self.add(t)

    # ------------------------------------------------------------------ build
    def add(self, task: Task) -> None:
        if task.id in self.tasks:
            raise GraphError(f"duplicate task id '{task.id}'")
        self.tasks[task.id] = task

    def validate(self, known_agents: set[str] | None = None) -> None:
        for t in self.tasks.values():
            for dep in t.depends_on:
                if dep not in self.tasks:
                    raise GraphError(f"task '{t.id}' depends on unknown task '{dep}'")
            if known_agents is not None and t.agent not in known_agents:
                raise GraphError(f"task '{t.id}' uses unknown agent '{t.agent}'")
        self.topological_order()  # raises on cycles

    def topological_order(self) -> list[str]:
        indeg = {tid: 0 for tid in self.tasks}
        children: dict[str, list[str]] = defaultdict(list)
        for t in self.tasks.values():
            for dep in t.depends_on:
                indeg[t.id] += 1
                children[dep].append(t.id)
        queue = deque(sorted(tid for tid, d in indeg.items() if d == 0))
        order: list[str] = []
        while queue:
            tid = queue.popleft()
            order.append(tid)
            for child in sorted(children[tid]):
                indeg[child] -= 1
                if indeg[child] == 0:
                    queue.append(child)
        if len(order) != len(self.tasks):
            cyclic = sorted(tid for tid, d in indeg.items() if d > 0)
            raise GraphError(f"dependency cycle detected among: {', '.join(cyclic)}")
        return order

    def layers(self) -> list[list[str]]:
        """Group tasks into waves that can run concurrently (for display/planning)."""
        depth: dict[str, int] = {}
        for tid in self.topological_order():
            deps = self.tasks[tid].depends_on
            depth[tid] = 0 if not deps else 1 + max(depth[d] for d in deps)
        out: dict[int, list[str]] = defaultdict(list)
        for tid, d in depth.items():
            out[d].append(tid)
        return [sorted(out[i]) for i in sorted(out)]

    # ------------------------------------------------------------ execution
    def _dep_satisfied(self, dep_id: str) -> bool:
        dep = self.tasks[dep_id]
        if dep.status == TaskStatus.DONE:
            return True
        # a non-critical task that failed does not hold up its dependents
        return dep.status in (TaskStatus.FAILED, TaskStatus.SKIPPED) and not dep.critical

    def ready(self) -> list[Task]:
        return [
            t for t in self.tasks.values()
            if t.status == TaskStatus.PENDING and all(self._dep_satisfied(d) for d in t.depends_on)
        ]

    def dependents(self, task_id: str) -> list[Task]:
        return [t for t in self.tasks.values() if task_id in t.depends_on]

    def block_dependents(self, task_id: str) -> list[str]:
        """Transitively mark everything downstream of a failed critical task as BLOCKED."""
        blocked: list[str] = []
        queue = deque([task_id])
        while queue:
            current = queue.popleft()
            for child in self.dependents(current):
                if child.status == TaskStatus.PENDING:
                    child.status = TaskStatus.BLOCKED
                    child.error = f"blocked: upstream task '{task_id}' failed"
                    blocked.append(child.id)
                    queue.append(child.id)
        return blocked

    def rewire(self, old_id: str, new_id: str, exclude: set[str]) -> list[str]:
        """Make every pending dependent of ``old_id`` wait on ``new_id`` instead.

        Used when a task hands off to dynamically spawned work (e.g. a failed
        validation spawns repair + re-validate; the reporter must wait for the
        *final* validation, not the first one).
        """
        changed = []
        for t in self.tasks.values():
            if t.id in exclude or t.id == new_id:
                continue
            if old_id in t.depends_on and t.status == TaskStatus.PENDING:
                t.depends_on = [new_id if d == old_id else d for d in t.depends_on]
                changed.append(t.id)
        return changed

    def unfinished(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)]

    def to_mermaid(self) -> str:
        style = {
            TaskStatus.DONE: ":::done", TaskStatus.FAILED: ":::failed",
            TaskStatus.BLOCKED: ":::blocked", TaskStatus.SKIPPED: ":::blocked",
        }
        lines = ["flowchart TD"]
        for t in self.tasks.values():
            label = t.title.replace('"', "'")
            lines.append(f'    {t.id}["{label}<br/><i>{t.agent}</i>"]{style.get(t.status, "")}')
        for t in self.tasks.values():
            for d in t.depends_on:
                lines.append(f"    {d} --> {t.id}")
        lines += [
            "    classDef done fill:#d4f7d4,stroke:#2e7d32",
            "    classDef failed fill:#ffd6d6,stroke:#c62828",
            "    classDef blocked fill:#eee,stroke:#999,stroke-dasharray: 4",
        ]
        return "\n".join(lines)
