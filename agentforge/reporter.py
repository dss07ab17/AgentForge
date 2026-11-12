"""Builds the final structured engineering summary (Markdown + JSON) from run state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import RunContext
from .models import TaskStatus
from .orchestrator.dag import TaskGraph


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(v: Any) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return out


def build_report(ctx: RunContext, graph: TaskGraph, meta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    bb = ctx.blackboard.snapshot()
    spec = bb.get("spec") or bb.get("spec_draft") or {}
    status = meta["status"]
    badge = {"success": "✅ SUCCESS", "needs_clarification": "⏸ NEEDS CLARIFICATION",
             "rejected": "⛔ REJECTED BY HUMAN", "failed": "❌ FAILED"}[status]
    L: list[str] = [f"# Engineering Summary: {spec.get('title', 'run')}", "",
                    f"**Outcome:** {badge}  ", f"**Requirement:** {meta['requirement']}  ",
                    f"**Run:** `{meta['run_id']}` · type `{spec.get('req_type', '?')}` · domain `{spec.get('domain', '?')}` · "
                    f"LLM `{meta['llm']}` ({meta['llm_calls']} calls) · approval mode `{meta['approval']}` · "
                    f"{meta['duration_s']}s", ""]
    if meta.get("message"):
        L += [f"> {meta['message']}", ""]

    # 1. problem ---------------------------------------------------------------
    if spec:
        L += ["## 1. Normalized engineering problem", "", f"**Intent:** {spec.get('intent', '')}", "",
              "**Functional requirements**", *[f"- {x}" for x in spec.get("functional", [])], "",
              "**Non-functional requirements**", *[f"- {x}" for x in spec.get("non_functional", [])], "",
              "**Out of scope**", *[f"- {x}" for x in spec.get("out_of_scope", [])], "",
              "**Acceptance criteria**", *[f"- [ ] {x}" for x in spec.get("acceptance_criteria", [])], ""]
        L += ["## 2. Ambiguities and how they were resolved", ""]
        L += _table(["id", "severity", "question", "resolution", "by"],
                    [[a["id"], a["severity"], a["question"], a.get("resolution") or "**UNRESOLVED**",
                      a.get("resolved_by") or "-"] for a in spec.get("ambiguities", [])])
        L.append("")

    # 3. codebase --------------------------------------------------------------
    impact = bb.get("impact")
    if impact:
        L += ["## 3. Codebase reasoning (brownfield impact analysis)", "",
              f"**Entry points:** {', '.join(impact['entry_points'])}  ",
              f"**APIs touched:** {', '.join(impact['apis']) or 'none'}  ",
              f"**Data stores:** {', '.join(impact['data_stores']) or 'none'}", "", "**Data flows**",
              *[f"- `{f}`" for f in impact["data_flows"]], "", "**Impacted modules** (implementation order: data layer first)"]
        L += _table(["module", "confidence", "why", "symbols"],
                    [[m["path"], m["confidence"], m["reason"], ", ".join(m["symbols"]) or "-"]
                     for m in impact["impacted_modules"]])
        L += ["", f"**Tests covering impacted code:** {', '.join(impact['tests_affected']) or 'none'}", "",
              "**Risk hotspots found by static analysis**",
              *[f"- `{h['file']}:{h['line']}` {h['issue']}: `{h['code']}`" for h in impact["risk_hotspots"]], ""]
        design = bb.get("change_design")
        if design:
            L += ["**Change design**"] + [f"- `{c['module']}`: {'; '.join(c['changes'])}" for c in design["module_changes"]]
            L += [f"- API changes: {'; '.join(design['api_changes']) or 'none'}",
                  f"- Data migration: {design['data_migration']}", f"- Compatibility: {design['compatibility']}", ""]

    # 4. plan ------------------------------------------------------------------
    L += ["## 4. Implementation plan and execution", "",
          "Tasks were decomposed by the Planner into a dependency graph and executed by the orchestrator; "
          "independent tasks in the same wave ran in parallel. Dynamic tasks (repair loops) were spawned at runtime.",
          "", "```mermaid", graph.to_mermaid(), "```", ""]
    L += _table(["task", "agent", "status", "attempts", "duration (s)", "depends on", "spawned by"],
                [[t.id, t.agent, t.status.value, t.attempts, "-" if t.duration is None else t.duration, ", ".join(t.depends_on) or "-",
                  t.spawned_by or "-"] for t in graph.tasks.values()])
    try:
        waves = graph.layers()
        L += ["", "**Parallel waves:** " + " → ".join("{" + ", ".join(w) + "}" for w in waves), ""]
    except Exception:  # noqa: BLE001
        L.append("")
    recov = [e for e in ctx.events.events
             if e["kind"] in ("task.retry", "task.failed", "task.blocked")
             or (e["kind"] == "task.spawned" and e.get("parent") != "plan")
             or (e["kind"] == "task.done" and str(e.get("task", "")).startswith("repair_"))
             or (e["kind"] == "info" and "fault injection" in e["message"])]
    recov = [{**e, "message": e["message"] + ": " + " | ".join(e.get("notes") or [])}  # show what repair fixed
             if e["kind"] == "task.done" else e for e in recov]
    if recov:
        L += ["**Error handling and recovery events**", *[f"- `{e['ts']}s` {e['message']}" for e in recov], ""]

    # 5. architecture ----------------------------------------------------------
    arch = bb.get("architecture")
    if arch:
        L += ["## 5. Architecture and rationale", "", f"**Style:** {arch['style']}", ""]
        L += _table(["component", "module", "responsibility"],
                    [[c["name"], c["module"], c["responsibility"]] for c in arch["components"]])
        L += ["", "**Key decisions**"]
        L += _table(["decision", "alternatives considered", "rationale"],
                    [[d["decision"], d["alternatives"], d["rationale"]] for d in arch["decisions"]])
        L += ["", "**Scaling path**", *[f"- {s}" for s in arch.get("scaling_path", [])], ""]

    # 6. artifacts -------------------------------------------------------------
    files = ctx.workspace.manifest
    if files:
        L += ["## 6. Generated artifacts", "", f"Location: `{meta['artifacts_path']}`", ""]
        L += _table(["file", "produced by", "bytes"], [[p, i["author"], i["bytes"]] for p, i in sorted(files.items())])
        contract = bb.get("api_contract")
        if contract:
            L += ["", "**API contract** (`api/openapi.json`)"]
            L += _table(["method", "path", "summary", "responses"],
                        [[m.upper(), p, op.get("summary", ""), ", ".join(op.get("responses", {}))]
                         for p, ops in contract["paths"].items() for m, op in ops.items()])
        schema = bb.get("data_schema")
        if schema:
            L += ["", f"**Schema:** `{schema['path']}` (tables: {', '.join(schema['tables'])})"]
        if meta.get("patch"):
            L += ["", f"**Patch for human review:** `{meta['patch']}` (apply with `git apply`)"]
        L.append("")

    # 7. validation ------------------------------------------------------------
    history = bb.get("validation_history") or []
    if history:
        L += ["## 7. Validation", "", "**Strategy:** independent validator re-checks the full tree each iteration: "
              "syntax → policy guardrails → lint → API contract conformance → unit + integration tests"
              + (" → regression against baseline" if bb.get("baseline_results") else "")
              + ". Failures spawn a bounded repair loop; the release gate waits for the final verdict.", ""]
        L += _table(["iteration", "result", *[c["name"] for c in history[-1]["checks"]]],
                    [[h["iteration"], "PASS" if h["passed"] else "FAIL",
                      *[("✔ " if c["passed"] else "✘ ") + c["details"] for c in h["checks"]]] for h in history])
        cov = bb.get("contract_coverage")
        if cov:
            L += ["", f"**Contract coverage by tests:** {cov['ratio']:.0%} of documented operations "
                      f"(uncovered: {', '.join(cov['uncovered']) or 'none'})"]
        base = bb.get("baseline_results")
        if base:
            L += ["", f"**Baseline (before change):** {base['passed']}/{base['total']} tests passing"]
        fails = (bb.get("test_results") or {}).get("failures") or []
        if fails and not history[-1]["passed"]:
            L += ["", "**Outstanding test failures**", *[f"- `{f['test']}`: {f['message']}" for f in fails[:10]]]
        L.append("")

    # 8. risks -----------------------------------------------------------------
    risks = bb.get("risks")
    if risks:
        L += ["## 8. Risks, trade-offs and failure scenarios", ""]
        L += _table(["risk", "category", "likelihood", "impact", "mitigation"],
                    [[r["title"], r["category"], r["likelihood"], r["impact"], r["mitigation"]] for r in risks["risks"]])
        if risks.get("tradeoffs"):
            L += ["", "**Trade-offs**"]
            L += _table(["choice", "gain", "cost"], [[t["choice"], t["gain"], t["cost"]] for t in risks["tradeoffs"]])
        if risks.get("failure_scenarios"):
            L += ["", "**Failure scenarios and designed behaviour**"]
            L += _table(["scenario", "behaviour"], [[f["scenario"], f["behaviour"]] for f in risks["failure_scenarios"]])
        L.append("")

    # 9. oversight -------------------------------------------------------------
    L += ["## 9. Human oversight (audit trail)", ""]
    L += _table(["gate", "decision", "by", "comment"],
                [[a["gate"], a["decision"], a["by"], a["comment"] or "-"] for a in ctx.gate.audit]) or ["none"]
    L += ["", "**Guardrails active:** workspace sandbox (no writes outside the run directory; original repo never "
          "modified), generated-code policy scan (eval/exec/shell/unsafe deserialisation/secrets), allow-listed "
          "test execution with timeout and scrubbed credentials, repairer may not edit tests or contract, budgets "
          f"(max {ctx.budgets.max_repair_iterations} repair iterations, {ctx.budgets.max_llm_calls} LLM calls, "
          f"{ctx.budgets.max_files} files).", ""]

    # 10. assumptions/limitations ---------------------------------------------
    L += ["## 10. Assumptions and limitations", "", "**Assumptions**", *[f"- {a}" for a in spec.get("assumptions", [])],
          "", "**Limitations**", *[f"- {x}" for x in meta["limitations"]], ""]

    payload = {"meta": meta, "spec": spec, "plan": bb.get("plan"), "tasks": [t.to_dict() for t in graph.tasks.values()],
               "impact": impact, "change_design": bb.get("change_design"), "architecture": arch,
               "validation_history": history, "risks": risks, "audit": ctx.gate.audit,
               "artifacts": files, "contract_coverage": bb.get("contract_coverage")}
    return "\n".join(L), payload


def limitations(spec: dict, provider: str) -> list[str]:
    out = []
    if provider == "offline":
        out.append("Offline mode: agents use deterministic strategies and domain blueprints (URL shortener, the "
                   "bundled brownfield sample). Other domains/repositories need `--llm anthropic`.")
    if spec.get("domain") == "url_shortener":
        out += ["Generated service uses SQLite and an in-process cache/queue: correct and tested, but not the "
                "production stack for the stated scale target (see scaling path).",
                "No authentication/authorization on the API (documented risk).",
                "Load/performance targets are design targets; no load test is executed by the pipeline."]
    out.append("Validation proves the generated tests pass and the contract matches; it cannot prove the tests are "
               "exhaustive - human review at G3 remains essential.")
    return out


def write_clarification_request(run_dir: Path, questions: list[dict], requirement: str) -> Path:
    lines = ["# Clarification needed before planning", "", f"Requirement: *{requirement}*", "",
             "The analyst found high-impact ambiguities. Building on a guess here would likely produce the wrong "
             "system, so the run stopped at gate G1.", ""]
    for q in questions:
        lines += [f"### `{q['id']}`: {q['question']}", f"*Why it matters:* {q['why_it_matters']}", "",
                  *[f"- {o}{' (suggested default)' if o == q['default'] else ''}" for o in q["options"]], ""]
    lines += ["Answer by editing `answers.template.json` and re-running with "
              "`--answers answers.template.json --resume <this run dir>` (or `--accept-defaults`)."]
    path = run_dir / "CLARIFICATION_REQUEST.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "answers.template.json").write_text(json.dumps(
        {"clarifications": {q["id"]: q["default"] for q in questions}}, indent=2) + "\n", encoding="utf-8",
        newline="\n")
    return path


def status_of(graph: TaskGraph, bb: dict) -> str:
    t = graph.tasks
    if "clarify" in t and t["clarify"].status == TaskStatus.FAILED and bb.get("clarification_request"):
        return "needs_clarification"
    if any(task.status == TaskStatus.FAILED and "rejected gate" in (task.error or "") for task in t.values()):
        return "rejected"
    if "approve_release" in t and t["approve_release"].status == TaskStatus.DONE:
        return "success"
    return "failed"
