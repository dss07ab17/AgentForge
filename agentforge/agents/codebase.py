"""Codebase Analyst (brownfield): static analysis of the existing system + impact analysis.

Works on any Python repository without an LLM:
* parses every module with ``ast`` -> symbols, docstrings, local imports,
  SQL statements, HTTP routes and status codes, test modules
* builds the module dependency graph and infers layers (entry points -> data)
* derives data flows (entry point -> ... -> storage) by walking the graph
* scores modules against the requirement's concepts and propagates impact to
  dependents (callers whose interface may change) and to the tests that cover them
* flags risky patterns relevant to the change (e.g. ``INSERT OR REPLACE``)
In LLM mode the structural summary is handed to the model for a deeper read.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from ..models import AgentResult, Task
from .base import Agent

CONCEPTS = {
    "expir": ["expir", "ttl", "time", "created"],
    "ttl": ["ttl", "expir", "time"],
    "overwrite": ["replace", "insert", "save", "store"],
    "duplicate": ["insert", "unique", "code"],
    "collision": ["code", "insert", "random"],
    "code": ["code", "new_code"],
    "link": ["link", "url", "save", "resolve"],
    "http": ["app", "route", "status", "environ"],
    "410": ["status", "environ", "start_response"],
    "status": ["start_response", "status"],
    "persist": ["sql", "connect", "commit"],
    "analytic": ["click", "stat", "count"],
    "test": ["test_"],
}
_SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE|ALTER TABLE)\b", re.I)
_TABLE = re.compile(r"(?:FROM|INTO|UPDATE|TABLE(?: IF NOT EXISTS)?)\s+(\w+)", re.I)
_ROUTE = re.compile(r"""^/[\w/{}<>-]*$""")
_RISKY = [
    (re.compile(r"INSERT OR REPLACE", re.I), "INSERT OR REPLACE silently overwrites existing rows on key conflict"),
    (re.compile(r"\bglobal\b"), "module-level mutable global state"),
    (re.compile(r"check_same_thread=False"), "SQLite connection shared across threads without a lock"),
]


def analyze_repository(root: Path) -> dict:
    modules: dict[str, dict] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") or part in {"__pycache__", "venv", ".venv"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            modules[rel] = {"error": f"syntax error line {exc.lineno}"}
            continue
        mod = rel[:-3].replace("/", ".").removesuffix(".__init__")
        info = {"path": rel, "module": mod, "doc": ast.get_docstring(tree) or "", "functions": [], "classes": [],
                "imports": set(), "sql": [], "tables": set(), "routes": set(), "statuses": set(),
                "is_test": rel.startswith("tests/") or Path(rel).name.startswith("test_"), "risks": [],
                "loc": len(source.splitlines())}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                info["functions"].append({"name": node.name, "line": node.lineno, "args": args,
                                          "doc": (ast.get_docstring(node) or "").split("\n")[0]})
            elif isinstance(node, ast.ClassDef):
                info["classes"].append({"name": node.name, "line": node.lineno,
                                        "bases": [ast.unparse(b) for b in node.bases]})
            elif isinstance(node, ast.Import):
                info["imports"].update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                info["imports"].add(node.module)
                info["imports"].update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if _SQL.search(text):
                    info["sql"].append(" ".join(text.split())[:160])
                    info["tables"].update(t for t in _TABLE.findall(text) if t.upper() not in {"IF", "SELECT"})
                if _ROUTE.match(text) and len(text) > 1:
                    info["routes"].add(text)
                m = re.match(r"^([1-5]\d\d) [A-Z]", text)
                if m:
                    info["statuses"].add(m.group(1))
        for lineno, line in enumerate(source.splitlines(), 1):
            for pattern, why in _RISKY:
                if pattern.search(line):
                    info["risks"].append({"line": lineno, "issue": why, "code": line.strip()[:120]})
        modules[rel] = info

    local = {info["module"]: rel for rel, info in modules.items() if "module" in info}
    graph: dict[str, set[str]] = defaultdict(set)
    for rel, info in modules.items():
        for imp in info.get("imports", ()):
            for candidate in (imp, imp.rsplit(".", 1)[0]):
                if candidate in local and local[candidate] != rel:
                    graph[rel].add(local[candidate])
    reverse: dict[str, set[str]] = defaultdict(set)
    for src, dsts in graph.items():
        for dst in dsts:
            reverse[dst].add(src)

    sources = [r for r, i in modules.items() if not i.get("is_test") and "module" in i]
    entry = sorted(r for r in sources if not (reverse[r] - {t for t in modules if modules[t].get("is_test")})
                   and (modules[r]["routes"] or modules[r]["statuses"] or graph[r]))
    flows = []

    def walk(node: str, path: list[str]) -> None:
        nexts = [n for n in sorted(graph[node]) if n not in path and not modules[n].get("is_test")]
        if not nexts:
            if len(path) > 1:
                flows.append(path)
            return
        for n in nexts:
            walk(n, path + [n])

    for e in entry:
        walk(e, [e])
    depth = {}
    for r in sources:
        depth[r] = max((len(f) - 1 - f.index(r) for f in flows if r in f), default=0)
    return {"modules": modules, "graph": graph, "reverse": reverse, "entry_points": entry,
            "flows": flows, "depth": depth}


def impact_analysis(requirement: str, analysis: dict) -> dict:
    text = requirement.lower()
    terms: set[str] = set()
    for concept, expansions in CONCEPTS.items():
        if concept in text:
            terms.update(expansions)
    terms.update(w for w in re.findall(r"[a-z_]{4,}", text) if w not in {"that", "with", "when", "where", "should",
                                                                          "also", "from", "must", "existing",
                                                                          "newly", "silently", "clients", "pass"})
    modules, reverse = analysis["modules"], analysis["reverse"]
    scored: dict[str, dict] = {}
    for rel, info in modules.items():
        if "module" not in info or info["is_test"]:
            continue
        if not info["functions"] and not info["classes"] and not info["sql"]:
            continue  # package markers / constants-only modules
        haystack = " ".join([Path(rel).stem, info["doc"], *(f["name"] + " " + f["doc"] + " " + " ".join(f["args"])
                                                  for f in info["functions"]),
                             *info["sql"], *info["routes"], *info["statuses"]]).lower()
        hits = sorted(t for t in terms if t in haystack)
        if hits:
            symbols = [f["name"] for f in info["functions"] if any(t in f["name"].lower() for t in hits)]
            scored[rel] = {"path": rel, "score": len(hits), "matched": hits, "symbols": symbols,
                           "reason": "direct match on " + ", ".join(hits[:6]), "confidence": "high" if len(hits) > 2 else "medium"}
    for rel in list(scored):  # callers of impacted modules may need interface changes
        for caller in reverse.get(rel, ()):
            if caller not in scored and not modules[caller]["is_test"]:
                scored[caller] = {"path": caller, "score": 0, "matched": [], "symbols": [],
                                  "reason": f"depends on {rel} (interface may change)", "confidence": "low"}
    # order leaves (data layer) first: the natural implementation order
    impacted = sorted(scored.values(), key=lambda m: (analysis["depth"].get(m["path"], 0), m["path"]))
    impacted_paths = {m["path"] for m in impacted}
    tests = sorted({r for r, i in modules.items() if i.get("is_test") and
                    any(dep in impacted_paths for dep in analysis["graph"].get(r, ()))})
    risks = [{"file": rel, **risk} for rel in impacted_paths for risk in modules[rel]["risks"]]
    apis = sorted({f"{route} ({rel})" for rel in impacted_paths for route in modules[rel]["routes"]})
    tables = sorted({t for rel in impacted_paths for t in modules[rel]["tables"]})
    return {
        "impacted_modules": impacted,
        "apis": apis,
        "data_stores": tables,
        "data_flows": [" -> ".join(f) for f in analysis["flows"]],
        "entry_points": analysis["entry_points"],
        "tests_affected": tests,
        "risk_hotspots": risks,
        "layers": {m["path"]: analysis["depth"].get(m["path"], 0) for m in impacted},
    }


def summarize(analysis: dict) -> dict:
    out = {}
    for rel, info in analysis["modules"].items():
        if "module" not in info:
            out[rel] = info
            continue
        out[rel] = {"doc": info["doc"].split("\n")[0], "loc": info["loc"], "test": info["is_test"],
                    "functions": [f"{f['name']}({', '.join(f['args'])})" for f in info["functions"]],
                    "classes": [c["name"] for c in info["classes"]],
                    "imports_local": sorted(analysis["graph"].get(rel, ())), "tables": sorted(info["tables"]),
                    "routes": sorted(info["routes"]), "http_statuses": sorted(info["statuses"]),
                    "sql": info["sql"][:4]}
    return out


class CodebaseAnalyst(Agent):
    name = "codebase_analyst"
    role = "Codebase Analyst"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        analysis = analyze_repository(ctx.workspace.root)
        impact = impact_analysis(task.params["requirement"], analysis)
        n_src = sum(1 for i in analysis["modules"].values() if not i.get("is_test"))
        notes = [f"{len(analysis['modules'])} modules ({n_src} source)",
                 f"impacted: {', '.join(m['path'] for m in impact['impacted_modules'])}",
                 f"flows: {impact['data_flows']}", f"hotspots: {len(impact['risk_hotspots'])}"]
        return AgentResult(outputs={"codebase": summarize(analysis), "impact": impact}, notes=notes)

    def run_llm(self, task: Task, ctx) -> AgentResult:
        base = self.run_offline(task, ctx)
        refined = self.ask(
            ctx, task, "Using the static analysis below, refine the impact analysis for the requirement: confirm or "
            "remove impacted modules, add missing ones, and describe the data flows and API changes.",
            '{"impacted_modules": [{"path": "...", "reason": "...", "confidence": "high|medium|low", "symbols": []}],'
            ' "apis": [], "data_stores": [], "data_flows": [], "notes": []}',
            Requirement=task.params["requirement"], Static_analysis=base.outputs["codebase"],
            Heuristic_impact=base.outputs["impact"])
        impact = {**base.outputs["impact"], **{k: v for k, v in refined.items() if k != "notes"}}
        return AgentResult(outputs={"codebase": base.outputs["codebase"], "impact": impact},
                           notes=base.notes + refined.get("notes", []))
