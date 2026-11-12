"""Planner: decomposes the clarified spec into an executable task DAG.

The planner does not run anything; it *spawns* the tasks into the live graph.
Every task depends (directly or transitively) on the ``approve_plan`` gate, so
no engineering work starts before a human (or policy) approves the plan.
"""
from __future__ import annotations

from pathlib import Path

from ..models import AgentResult, RequirementType, Task
from ..orchestrator.dag import TaskGraph
from .base import Agent

AGENT_VOCABULARY = {
    "architect": "designs architecture (mode=architecture), API contract (mode=contract), data schema (mode=schema) or a brownfield change design (mode=change)",
    "coder": "implements code for a component (params.component) or edits existing files (params.files)",
    "tester": "writes unit/integration tests (params.component = unit_tests|integration_tests|brownfield)",
    "docs": "writes or updates documentation",
    "risk": "analyses risks, trade-offs and failure scenarios",
    "validator": "runs syntax/policy/lint/contract/test/regression checks (params.mode = validate|baseline)",
}


def T(id, title, agent, deps, description="", **params) -> Task:  # noqa: N802 - tiny DSL
    outputs = params.pop("outputs", [])
    critical = params.pop("critical", True)
    return Task(id=id, title=title, agent=agent, depends_on=deps, description=description, params=params,
                outputs=outputs, critical=critical)


def url_shortener_plan() -> list[Task]:
    return [
        T("design_architecture", "Design system architecture", "architect", ["approve_plan"],
          "Components, key decisions with alternatives, data model, scaling path", mode="architecture",
          outputs=["architecture"]),
        T("define_api_contract", "Define REST API contract (OpenAPI 3)", "architect", ["design_architecture"],
          mode="contract", outputs=["api_contract"]),
        T("define_data_schema", "Define persistence schema (SQL DDL)", "architect", ["design_architecture"],
          mode="schema", outputs=["data_schema"]),
        T("impl_core", "Implement core: config, codec, id generation, validation, models, errors", "coder",
          ["design_architecture"], component="core"),
        T("impl_storage", "Implement repository + redirect cache", "coder", ["impl_core", "define_data_schema"],
          component="storage"),
        T("impl_analytics", "Implement async click analytics", "coder", ["impl_storage"], component="analytics"),
        T("impl_service", "Implement service layer (+ rate limiter)", "coder", ["impl_storage", "impl_analytics"],
          component="service"),
        T("impl_api", "Implement HTTP API conforming to the contract", "coder", ["impl_service", "define_api_contract"],
          component="api"),
        T("write_unit_tests", "Write unit tests from the design", "tester", ["design_architecture"],
          component="unit_tests"),
        T("write_integration_tests", "Write integration + contract tests", "tester", ["define_api_contract"],
          component="integration_tests"),
        T("write_docs", "Write README + architecture docs", "docs", ["define_api_contract", "define_data_schema"]),
        T("analyze_risks", "Analyse risks, trade-offs, failure scenarios", "risk", ["design_architecture"],
          critical=False),
        T("validate", "Validate: syntax, policy, lint, contract, tests", "validator",
          ["impl_api", "write_unit_tests", "write_integration_tests"], mode="validate", iteration=1),
        T("approve_release", "G3: approve validated output", "gate", ["validate", "write_docs", "analyze_risks"],
          gate="release"),
    ]


def brownfield_plan(impact: dict, graph_edges: dict[str, list[str]]) -> list[Task]:
    modules = [m for m in impact["impacted_modules"] if m["confidence"] != "low" or m["path"] in graph_edges]
    impl_ids = {m["path"]: f"impl_{Path(m['path']).stem}" for m in modules}
    tasks = [
        T("design_change", "Design the change (per-module plan, API + data migration)", "architect",
          ["approve_plan"], mode="change", outputs=["change_design"]),
        T("baseline_tests", "Run existing test suite on the untouched baseline", "validator", ["approve_plan"],
          mode="baseline", outputs=["baseline_results"]),
    ]
    for m in modules:
        deps = ["design_change"] + [impl_ids[d] for d in graph_edges.get(m["path"], []) if d in impl_ids]
        tasks.append(T(impl_ids[m["path"]], f"Change {m['path']}", "coder", deps,
                       m["reason"], files=[m["path"]]))
    impl = list(impl_ids.values())
    tasks += [
        T("update_tests", "Add regression + feature tests", "tester", ["design_change"], component="brownfield"),
        T("update_docs", "Update README / changelog", "docs", impl or ["design_change"]),
        T("analyze_risks", "Analyse risks, trade-offs, failure scenarios", "risk", ["design_change"], critical=False),
        T("validate", "Validate: syntax, policy, lint, tests, regression vs baseline", "validator",
          impl + ["update_tests", "baseline_tests"], mode="validate", iteration=1),
        T("approve_release", "G3: approve the diff", "gate", ["validate", "update_docs", "analyze_risks"],
          gate="release"),
    ]
    return tasks


def generic_plan(spec) -> list[Task]:
    return [
        T("design_architecture", "Design architecture", "architect", ["approve_plan"], mode="architecture",
          outputs=["architecture"]),
        T("define_api_contract", "Define interfaces/contract", "architect", ["design_architecture"], mode="contract",
          outputs=["api_contract"]),
        T("impl_app", "Implement the application", "coder", ["define_api_contract"], component="app"),
        T("write_tests", "Write unit + integration tests", "tester", ["define_api_contract"], component="tests"),
        T("write_docs", "Write documentation", "docs", ["define_api_contract"]),
        T("analyze_risks", "Analyse risks", "risk", ["design_architecture"], critical=False),
        T("validate", "Validate", "validator", ["impl_app", "write_tests"], mode="validate", iteration=1),
        T("approve_release", "G3: approve output", "gate", ["validate", "write_docs", "analyze_risks"], gate="release"),
    ]


class Planner(Agent):
    name = "planner"
    role = "Technical Planner"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        if spec.req_type in (RequirementType.BROWNFIELD, RequirementType.TESTS_DOCS):
            codebase = ctx.blackboard.require("codebase")
            edges = {path: info.get("imports_local", []) for path, info in codebase.items()}
            tasks = brownfield_plan(ctx.blackboard.require("impact"), edges)
        elif spec.domain == "url_shortener":
            tasks = url_shortener_plan()
        else:
            tasks = generic_plan(spec)
        return self._finish(tasks)

    def run_llm(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        if spec.domain == "url_shortener" or spec.req_type != RequirementType.GREENFIELD:
            # known shapes: keep the vetted plan template, it is already well-decomposed
            return self.run_offline(task, ctx)
        data = self.ask(ctx, task, "Decompose this spec into engineering tasks. Use only these agents:\n"
                        + "\n".join(f"- {k}: {v}" for k, v in AGENT_VOCABULARY.items())
                        + "\nThe first tasks must depend on 'approve_plan'. Include a validator task with "
                        "mode=validate and iteration=1 that depends on all code and test tasks.",
                        '{"tasks": [{"id": "snake_case", "title": "...", "agent": "...", "depends_on": ["..."], '
                        '"description": "...", "params": {}}]}', Spec=spec.to_dict(),
                        Impact=ctx.blackboard.get("impact"))
        tasks = [Task(id=t["id"], title=t["title"], agent=t["agent"], depends_on=t.get("depends_on", []),
                      description=t.get("description", ""), params=t.get("params", {})) for t in data["tasks"]]
        tasks.append(T("approve_release", "G3: approve output", "gate",
                       [t.id for t in tasks if t.agent == "validator"], gate="release"))
        return self._finish(tasks)

    @staticmethod
    def _finish(tasks: list[Task]) -> AgentResult:
        gate = T("approve_plan", "G2: approve spec + plan", "gate", ["plan"], gate="plan")
        candidate = TaskGraph([Task(id="plan", title="plan", agent="planner"), gate, *[Task(**{
            **t.__dict__}) for t in tasks]])
        candidate.validate()  # unknown deps / cycles -> retried with feedback
        layers = candidate.layers()
        plan = [{"id": t.id, "title": t.title, "agent": t.agent, "depends_on": t.depends_on} for t in tasks]
        return AgentResult(outputs={"plan": plan, "plan_layers": layers}, spawn=[gate, *tasks],
                           notes=[f"{len(tasks)} tasks in {len(layers) - 2} waves after approval"])
