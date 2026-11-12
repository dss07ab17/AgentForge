"""Top-level workflow: sets up a run, executes the task graph, and always finalizes.

Initial graph (the planner extends it at runtime):

    analyze ─┐
             ├─> clarify (G1) ─> plan ─> approve_plan (G2) ─> ...engineering DAG... ─> approve_release (G3)
    codebase ┘   (brownfield only; runs in parallel with analyze)
"""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .agents import registry
from .context import FaultInjector, RunContext
from .guardrails import Budgets
from .hitl import Gatekeeper
from .llm import build_provider
from .models import Task, TaskStatus
from .orchestrator.blackboard import Blackboard
from .orchestrator.dag import TaskGraph
from .orchestrator.engine import Engine
from .orchestrator.events import EventLog
from .reporter import build_report, limitations, status_of, write_clarification_request
from .workspace import Workspace

EXIT_CODES = {"success": 0, "failed": 2, "needs_clarification": 3, "rejected": 4}


@dataclass
class RunOutcome:
    status: str
    run_dir: Path
    report: Path
    exit_code: int


def initial_graph(requirement: str, has_repo: bool) -> TaskGraph:
    tasks = [Task("analyze", "Analyse and normalize the requirement", "analyst",
                  params={"requirement": requirement, "has_repo": has_repo}, outputs=["spec"])]
    clarify_deps = ["analyze"]
    if has_repo:
        tasks.append(Task("codebase", "Analyse existing codebase + impact", "codebase_analyst",
                          params={"requirement": requirement}, outputs=["codebase", "impact"]))
        clarify_deps.append("codebase")
    tasks += [
        Task("clarify", "G1: resolve ambiguities", "clarifier", depends_on=clarify_deps, outputs=["spec"],
             max_attempts=1),
        Task("plan", "Decompose into a task graph", "planner", depends_on=["clarify"], outputs=["plan"]),
    ]
    return TaskGraph(tasks)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def run(requirement: str | None = None, *, repo: Path | None = None, out_dir: Path = Path("runs"),
        llm: str = "auto", approval: str = "auto", answers: Path | None = None, accept_defaults: bool = False,
        faults: list[str] | None = None, max_workers: int = 4, resume: Path | None = None,
        verbose: bool = True, run_name: str | None = None) -> RunOutcome:
    budgets = Budgets()
    t0 = time.time()
    state = None
    if resume:
        run_dir = resume
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        requirement, repo = state["requirement"], Path(state["repo"]) if state["repo"] else None
    else:
        run_id = run_name or f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(requirement or 'run')}"
        run_dir = out_dir / run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
    assert requirement, "a requirement is required"

    events = EventLog(run_dir / "trace.jsonl", verbose=verbose)
    workspace = (Workspace.from_repo(repo.resolve(), run_dir, budgets) if repo
                 else Workspace(run_dir / "workspace", budgets))
    provider = build_provider(llm, budgets.max_llm_calls)
    gate = Gatekeeper.build(approval, answers, accept_defaults)
    ctx = RunContext(run_dir=run_dir.resolve(), blackboard=Blackboard(), events=events, workspace=workspace,
                     llm=provider, gate=gate, budgets=budgets, faults=FaultInjector.parse(faults), repo=repo)

    if state:
        graph = TaskGraph([Task.from_dict(t) for t in state["tasks"]])
        for t in graph.tasks.values():
            if t.status != TaskStatus.DONE:
                t.status, t.attempts, t.error = TaskStatus.PENDING, 0, None
        ctx.blackboard.restore(state["blackboard"], state["history"])
        gate.audit = state["audit"]
        workspace.manifest = state["manifest"]
        events.emit("run.start", f"resuming {run_dir.name}: {sum(t.status == TaskStatus.DONE for t in graph.tasks.values())} "
                                 f"task(s) already done")
    else:
        graph = initial_graph(requirement, repo is not None)
        events.emit("run.start", f"requirement: {requirement!r} | llm={provider.name} approval={gate.mode}"
                                 + (f" | repo={repo}" if repo else ""))

    def checkpoint(g: TaskGraph) -> None:
        (run_dir / "state.json").write_text(json.dumps({
            "requirement": requirement, "repo": str(repo) if repo else None,
            "tasks": [t.to_dict() for t in g.tasks.values()], "blackboard": ctx.blackboard.snapshot(),
            "history": ctx.blackboard.history(), "audit": gate.audit, "manifest": workspace.manifest,
        }, indent=1, default=str), encoding="utf-8", newline="\n")

    engine = Engine(ctx, registry(), max_workers=max_workers, on_checkpoint=checkpoint)
    engine.run(graph)
    return finalize(ctx, graph, requirement, repo, provider.name, gate.mode, t0)


def finalize(ctx: RunContext, graph: TaskGraph, requirement: str, repo: Path | None, provider: str, approval: str,
             t0: float) -> RunOutcome:
    bb = ctx.blackboard.snapshot()
    status = status_of(graph, bb)
    run_dir = ctx.run_dir
    message, patch = "", None
    if status == "needs_clarification":
        path = write_clarification_request(run_dir, bb["clarification_request"], requirement)
        message = f"Stopped at G1: {len(bb['clarification_request'])} blocking question(s). See {path.name}."
    elif status == "success" and repo:
        diff = ctx.workspace.diff()
        patch = run_dir / "changes.patch"
        patch.write_text(diff, encoding="utf-8", newline="\n")
        message = f"Validated change exported as {patch.name}; the original repository was not modified."
    elif status == "success":
        message = "Generated project is in workspace/ (cd there and run `python -m pytest`)."
    elif status == "failed":
        failed = [t.id for t in graph.tasks.values() if t.status == TaskStatus.FAILED]
        message = f"Failed tasks: {', '.join(failed) or 'none'}; downstream work was blocked. Partial results below."
    meta = {"run_id": run_dir.name, "requirement": requirement, "status": status, "llm": provider,
            "llm_calls": ctx.llm.calls, "approval": approval, "duration_s": round(time.time() - t0, 2),
            "artifacts_path": "workspace/", "patch": patch.name if patch else None, "message": message,
            "limitations": limitations(bb.get("spec") or bb.get("spec_draft") or {}, provider)}
    markdown, payload = build_report(ctx, graph, meta)
    report = run_dir / "ENGINEERING_SUMMARY.md"
    report.write_text(markdown + "\n", encoding="utf-8", newline="\n")
    (run_dir / "summary.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8", newline="\n")
    (run_dir / "plan.mmd").write_text(graph.to_mermaid() + "\n", encoding="utf-8", newline="\n")
    ctx.events.emit("run.end", f"{status.upper()} in {meta['duration_s']}s -> {report}")
    return RunOutcome(status=status, run_dir=run_dir, report=report, exit_code=EXIT_CODES[status])
