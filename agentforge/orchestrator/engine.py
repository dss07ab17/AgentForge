"""Execution engine: runs a TaskGraph with parallelism, retries and repair loops.

Execution model
---------------
* Every task whose dependencies are satisfied is submitted to a thread pool,
  so independent branches (e.g. risk analysis, docs, unit tests) run
  concurrently.
* A task's agent returns an ``AgentResult``. The engine checks that the task
  published every blackboard key it promised (an output contract), writes files
  through the guarded workspace, merges outputs into the blackboard and adds any
  spawned tasks to the live graph.
* Failures are retried with exponential backoff and the previous error is made
  available to the agent (``task.error``) so it can self-correct.
  ``NonRetriableError`` (e.g. a human rejected a gate, or a guardrail fired on
  something retrying cannot fix) fails immediately.
* A critical task that fails for good transitively BLOCKS its dependents, while
  unrelated branches keep running; the final report shows partial results
  instead of the whole run crashing.
* After every state change the run is checkpointed so ``--resume`` can pick up
  where it stopped.
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Callable

from ..models import AgentResult, Task, TaskStatus
from .dag import TaskGraph

if TYPE_CHECKING:
    from ..agents.base import Agent
    from ..context import RunContext


class NonRetriableError(RuntimeError):
    """Failure that retrying will not fix (human rejection, hard policy violation)."""


class OutputContractError(RuntimeError):
    pass


class Engine:
    def __init__(
        self,
        ctx: "RunContext",
        agents: dict[str, "Agent"],
        *,
        max_workers: int = 4,
        base_backoff: float = 0.2,
        on_checkpoint: Callable[[TaskGraph], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.agents = agents
        self.max_workers = max_workers
        self.base_backoff = base_backoff
        self.on_checkpoint = on_checkpoint
        self._not_before: dict[str, float] = {}

    # ---------------------------------------------------------------- public
    def run(self, graph: TaskGraph) -> TaskGraph:
        graph.validate(set(self.agents))
        ev = self.ctx.events
        ev.emit("stage", f"Executing task graph: {len(graph.tasks)} tasks in {len(graph.layers())} dependency waves")
        futures: dict[Future, Task] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="agent") as pool:
            while True:
                now = time.time()
                for task in graph.ready():
                    if self._not_before.get(task.id, 0) > now or len(futures) >= self.max_workers:
                        continue
                    task.status = TaskStatus.RUNNING
                    task.attempts += 1
                    task.started_at = task.started_at or time.time()
                    attempt = f" (attempt {task.attempts}/{task.max_attempts})" if task.attempts > 1 else ""
                    ev.emit("task.start", f"{task.id} [{task.agent}] {task.title}{attempt}", task=task.id)
                    futures[pool.submit(self._execute, task)] = task

                if not futures:
                    waiting = [t for t in graph.ready() if self._not_before.get(t.id, 0) > now]
                    if waiting:  # only backoff timers left: sleep until the earliest one
                        time.sleep(max(0.0, min(self._not_before[t.id] for t in waiting) - now))
                        continue
                    break

                done, _ = wait(list(futures), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    task = futures.pop(fut)
                    try:
                        result = fut.result()
                    except Exception as exc:  # noqa: BLE001 - agent failures are data here
                        self._on_failure(graph, task, exc)
                    else:
                        self._on_success(graph, task, result)
                    self._checkpoint(graph)

        for t in graph.unfinished():  # deps never satisfied (should be rare: blocked chains)
            t.status = TaskStatus.SKIPPED
            t.error = t.error or "skipped: dependencies never satisfied"
        self._checkpoint(graph)
        return graph

    # --------------------------------------------------------------- private
    def _execute(self, task: Task) -> AgentResult:
        agent = self.agents[task.agent]
        result = agent.run(task, self.ctx)
        if not isinstance(result, AgentResult):
            raise OutputContractError(f"agent '{task.agent}' returned {type(result).__name__}, expected AgentResult")
        missing = [k for k in task.outputs if k not in result.outputs]
        if missing:
            raise OutputContractError(f"task '{task.id}' did not publish required outputs: {missing}")
        # side effects go through the guarded workspace (policy checks + sandbox)
        for path, content in result.files.items():
            self.ctx.workspace.write(path, content, author=task.id)
        if result.edits:
            self.ctx.workspace.apply_edits(result.edits, author=task.id)
        return result

    def _on_success(self, graph: TaskGraph, task: Task, result: AgentResult) -> None:
        bb, ev = self.ctx.blackboard, self.ctx.events
        for key, value in result.outputs.items():
            bb.put(key, value, author=task.id)
        spawned_ids = set()
        for new in result.spawn:
            new.spawned_by = task.id
            graph.add(new)
            spawned_ids.add(new.id)
            ev.emit("task.spawned", f"{task.id} spawned {new.id} [{new.agent}] {new.title}", task=new.id, parent=task.id)
        if result.handoff:
            rewired = graph.rewire(task.id, result.handoff, exclude=spawned_ids)
            if rewired:
                ev.emit("info", f"rewired {', '.join(rewired)} to wait on {result.handoff}")
        graph.validate(set(self.agents))
        task.status = TaskStatus.DONE
        task.error = None
        task.finished_at = time.time()
        files = f", {len(result.files)} file(s)" if result.files else ""
        edits = f", {len(result.edits)} edit(s)" if result.edits else ""
        ev.emit("task.done", f"{task.id} done in {task.duration}s{files}{edits}", task=task.id, notes=result.notes)

    def _on_failure(self, graph: TaskGraph, task: Task, exc: BaseException) -> None:
        ev = self.ctx.events
        task.error = f"{type(exc).__name__}: {exc}"
        retriable = not isinstance(exc, NonRetriableError)
        if retriable and task.attempts < task.max_attempts:
            delay = self.base_backoff * (2 ** (task.attempts - 1))
            self._not_before[task.id] = time.time() + delay
            task.status = TaskStatus.PENDING
            ev.emit("task.retry", f"{task.id} failed ({task.error}); retrying in {delay:.1f}s", task=task.id)
            return
        task.status = TaskStatus.FAILED
        task.finished_at = time.time()
        ev.emit("task.failed", f"{task.id} FAILED after {task.attempts} attempt(s): {task.error}",
                task=task.id, trace=traceback.format_exception(exc)[-3:])
        if task.critical:
            blocked = graph.block_dependents(task.id)
            for b in blocked:
                ev.emit("task.blocked", f"{b} blocked by failure of {task.id}", task=b)

    def _checkpoint(self, graph: TaskGraph) -> None:
        if self.on_checkpoint:
            self.on_checkpoint(graph)
