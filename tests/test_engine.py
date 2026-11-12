"""Orchestrator behaviour: parallelism, retries, failure propagation, dynamic tasks, contracts."""
from __future__ import annotations

import threading
import time

import pytest

from agentforge.context import FaultInjector, RunContext
from agentforge.guardrails import Budgets
from agentforge.hitl import Gatekeeper
from agentforge.llm import OfflineProvider
from agentforge.models import AgentResult, Task, TaskStatus
from agentforge.orchestrator.blackboard import Blackboard
from agentforge.orchestrator.dag import GraphError, TaskGraph
from agentforge.orchestrator.engine import Engine, NonRetriableError
from agentforge.orchestrator.events import EventLog
from agentforge.workspace import Workspace


class FnAgent:
    """Test double: runs a function as the agent body."""

    def __init__(self, fn):
        self.fn = fn

    def run(self, task, ctx):
        return self.fn(task, ctx)


@pytest.fixture
def ctx(tmp_path):
    return RunContext(run_dir=tmp_path, blackboard=Blackboard(), events=EventLog(None, verbose=False),
                      workspace=Workspace(tmp_path / "ws", Budgets()), llm=OfflineProvider(), gate=Gatekeeper(),
                      budgets=Budgets(), faults=FaultInjector())


def test_cycle_and_unknown_dependency_are_rejected():
    with pytest.raises(GraphError, match="cycle"):
        TaskGraph([Task("a", "a", "x", depends_on=["b"]), Task("b", "b", "x", depends_on=["a"])]).validate()
    with pytest.raises(GraphError, match="unknown task"):
        TaskGraph([Task("a", "a", "x", depends_on=["zzz"])]).validate()


def test_independent_tasks_run_in_parallel(ctx):
    active, peak, lock = [0], [0], threading.Lock()

    def slow(task, _):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        with lock:
            active[0] -= 1
        return AgentResult()

    graph = TaskGraph([Task(f"t{i}", "t", "slow") for i in range(4)] + [Task("join", "j", "slow", depends_on=["t0", "t1", "t2", "t3"])])
    Engine(ctx, {"slow": FnAgent(slow)}, max_workers=4).run(graph)
    assert peak[0] >= 3
    assert all(t.status == TaskStatus.DONE for t in graph.tasks.values())


def test_transient_failure_is_retried_with_error_feedback(ctx):
    seen_errors = []

    def flaky(task, _):
        seen_errors.append(task.error)
        if task.attempts < 3:
            raise TimeoutError("upstream timeout")
        return AgentResult(outputs={"x": 1})

    graph = TaskGraph([Task("a", "a", "flaky", outputs=["x"])])
    Engine(ctx, {"flaky": FnAgent(flaky)}, base_backoff=0.01).run(graph)
    assert graph.tasks["a"].status == TaskStatus.DONE and graph.tasks["a"].attempts == 3
    assert seen_errors[1] == "TimeoutError: upstream timeout"  # previous error visible to the agent


def test_permanent_failure_blocks_dependents_but_not_independent_branches(ctx):
    def ok(task, _):
        return AgentResult()

    def boom(task, _):
        raise NonRetriableError("hard failure")

    graph = TaskGraph([Task("bad", "b", "boom"), Task("child", "c", "ok", depends_on=["bad"]),
                       Task("grandchild", "g", "ok", depends_on=["child"]), Task("other", "o", "ok")])
    Engine(ctx, {"ok": FnAgent(ok), "boom": FnAgent(boom)}).run(graph)
    assert graph.tasks["bad"].attempts == 1  # non-retriable: no retry
    assert graph.tasks["child"].status == TaskStatus.BLOCKED
    assert graph.tasks["grandchild"].status == TaskStatus.BLOCKED
    assert graph.tasks["other"].status == TaskStatus.DONE


def test_non_critical_failure_does_not_block(ctx):
    def boom(task, _):
        raise NonRetriableError("nope")

    graph = TaskGraph([Task("opt", "o", "boom", critical=False), Task("next", "n", "ok", depends_on=["opt"])])
    Engine(ctx, {"boom": FnAgent(boom), "ok": FnAgent(lambda t, c: AgentResult())}).run(graph)
    assert graph.tasks["next"].status == TaskStatus.DONE


def test_output_contract_is_enforced(ctx):
    graph = TaskGraph([Task("a", "a", "lazy", outputs=["promised"], max_attempts=2)])
    Engine(ctx, {"lazy": FnAgent(lambda t, c: AgentResult())}, base_backoff=0.01).run(graph)
    assert graph.tasks["a"].status == TaskStatus.FAILED
    assert "did not publish required outputs" in graph.tasks["a"].error


def test_spawned_tasks_and_handoff_rewire_dependents(ctx):
    order = []

    def validate(task, _):
        order.append(task.id)
        if task.id == "validate":
            return AgentResult(spawn=[Task("fix", "fix", "rec", depends_on=["validate"]),
                                      Task("validate2", "v2", "validate", depends_on=["fix"])], handoff="validate2")
        return AgentResult()

    def rec(task, _):
        order.append(task.id)
        return AgentResult()

    graph = TaskGraph([Task("validate", "v", "validate"), Task("release", "r", "rec", depends_on=["validate"])])
    Engine(ctx, {"validate": FnAgent(validate), "rec": FnAgent(rec)}).run(graph)
    assert order == ["validate", "fix", "validate2", "release"]
    assert graph.tasks["release"].depends_on == ["validate2"]


def test_blackboard_coordination_and_history(ctx):
    graph = TaskGraph([
        Task("producer", "p", "p", outputs=["contract"]),
        Task("consumer", "c", "c", depends_on=["producer"], outputs=["impl"]),
    ])
    agents = {"p": FnAgent(lambda t, c: AgentResult(outputs={"contract": {"ops": 2}})),
              "c": FnAgent(lambda t, c: AgentResult(outputs={"impl": c.blackboard.require("contract")["ops"] * 10}))}
    Engine(ctx, agents).run(graph)
    assert ctx.blackboard.get("impl") == 20
    assert [h["author"] for h in ctx.blackboard.history()] == ["producer", "consumer"]


def test_checkpoint_called_after_each_task(ctx):
    calls = []
    graph = TaskGraph([Task("a", "a", "ok"), Task("b", "b", "ok", depends_on=["a"])])
    Engine(ctx, {"ok": FnAgent(lambda t, c: AgentResult())}, on_checkpoint=lambda g: calls.append(1)).run(graph)
    assert len(calls) >= 2
