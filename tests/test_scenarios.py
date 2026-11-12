"""End-to-end runs of the full pipeline (offline, deterministic)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentforge import workflow
from agentforge.cli import SCENARIOS
from agentforge.llm.base import LLMProvider
from agentforge.models import TaskStatus

ROOT = Path(__file__).parents[1]


def run(name, tmp_path, **overrides):
    sc = {**SCENARIOS[name], **overrides}
    return workflow.run(sc["requirement"], repo=sc.get("repo"), out_dir=tmp_path, llm="offline", approval="auto",
                        answers=sc.get("answers"), faults=sc.get("faults"), verbose=False, run_name=name,
                        accept_defaults=sc.get("accept_defaults", False))


def summary(outcome):
    return json.loads((outcome.run_dir / "summary.json").read_text(encoding="utf-8"))


def test_greenfield_url_shortener(tmp_path):
    out = run("greenfield", tmp_path)
    assert out.status == "success" and out.exit_code == 0
    s = summary(out)
    assert s["validation_history"][-1]["passed"]
    tests = next(c for c in s["validation_history"][-1]["checks"] if c["name"] == "tests")
    assert tests["data"]["total"] >= 60
    ws = out.run_dir / "workspace"
    for f in ["api/openapi.json", "shortener/storage/schema.sql", "shortener/api.py", "docs/ARCHITECTURE.md",
              "tests/integration/test_contract.py"]:
        assert (ws / f).exists(), f
    assert [a["gate"] for a in s["audit"] if not a["gate"].startswith("clarify:")] == ["plan", "release"]
    # the generated project's tests also pass when run independently
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout[-2000:]


def test_brownfield_change_with_regression_gate(tmp_path):
    original = (ROOT / "examples/brownfield/linkshort_v1/app/store.py").read_text(encoding="utf-8")
    out = run("brownfield", tmp_path)
    assert out.status == "success"
    s = summary(out)
    checks = {c["name"]: c for c in s["validation_history"][-1]["checks"]}
    assert checks["regression"]["passed"] and checks["regression"]["data"]["baseline_passed"] == 3
    patch = (out.run_dir / "changes.patch").read_text(encoding="utf-8")
    assert "-        \"INSERT OR REPLACE INTO links" in patch and ".ruff_cache" not in patch
    store = ROOT / "examples/brownfield/linkshort_v1/app/store.py"
    assert store.read_text(encoding="utf-8") == original  # never modified
    # the exported patch applies cleanly to a pristine copy of the repo
    pristine = tmp_path / "pristine"
    shutil.copytree(ROOT / "examples/brownfield/linkshort_v1", pristine)
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=pristine, check=True)
        check = subprocess.run(["git", "apply", "--check", str(out.run_dir / "changes.patch")], cwd=pristine,
                               capture_output=True, text=True, encoding="utf-8")
        assert check.returncode == 0, check.stderr


def test_runs_nested_inside_a_project_with_its_own_pytest_config(tmp_path):
    """Regression: an ancestor pytest config used to change test ids and break the baseline comparison."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
                                             encoding="utf-8")
    out = run("brownfield", tmp_path / "runs")
    checks = {c["name"]: c for c in summary(out)["validation_history"][-1]["checks"]}
    assert out.status == "success", checks
    assert checks["regression"]["data"]["baseline_passed"] == 3


def test_ambiguous_requirement_stops_for_clarification(tmp_path):
    out = run("ambiguous", tmp_path)
    assert out.status == "needs_clarification" and out.exit_code == 3
    template = json.loads((out.run_dir / "answers.template.json").read_text(encoding="utf-8"))
    assert set(template["clarifications"]) == {"system", "analytics", "users"}


def test_ambiguous_resume_with_answers(tmp_path):
    first = run("ambiguous", tmp_path)
    resumed = workflow.run(None, resume=first.run_dir, llm="offline", approval="auto", verbose=False,
                           answers=ROOT / "examples/answers/ambiguous_answers.json")
    assert resumed.status == "success"
    ws = first.run_dir / "workspace"
    assert "expires" not in (ws / "shortener/service.py").read_text(encoding="utf-8")      # expiry answered "no"
    assert "custom_alias" not in (ws / "api/openapi.json").read_text(encoding="utf-8")    # aliases answered "no"
    assert "click_events" not in (ws / "shortener/storage/schema.sql").read_text(encoding="utf-8")  # basic analytics


def test_recovery_from_transient_failure_and_planted_bug(tmp_path):
    out = run("recovery", tmp_path)
    assert out.status == "success"
    s = summary(out)
    tasks = {t["id"]: t for t in s["tasks"]}
    assert tasks["impl_storage"]["attempts"] == 2
    assert [h["passed"] for h in s["validation_history"]] == [False, True]
    assert tasks["repair_1"]["spawned_by"] == "validate"
    assert "validate_2" in tasks["approve_release"]["depends_on"]


def test_rejected_release_gate(tmp_path):
    answers = tmp_path / "reject.json"
    answers.write_text(json.dumps({"approvals": {"release": False}}), encoding="utf-8")
    out = run("greenfield", tmp_path, answers=answers)
    assert out.status == "rejected" and out.exit_code == 4


def test_unrecoverable_failure_blocks_release_and_still_reports(tmp_path):
    out = run("greenfield", tmp_path, faults=["impl_core:transient", "design_architecture:transient"])
    assert out.status == "success"  # transient faults recovered
    # a domain with no offline blueprint fails at design time but still produces a report
    out = workflow.run("Build a payroll system with tax rules", out_dir=tmp_path, llm="offline", approval="auto",
                       verbose=False, run_name="payroll", accept_defaults=True)
    assert out.status == "failed"
    s = summary(out)
    statuses = {t["id"]: t["status"] for t in s["tasks"]}
    assert statuses["design_architecture"] == TaskStatus.FAILED.value
    assert statuses["approve_release"] == TaskStatus.BLOCKED.value
    assert out.report.exists()


class ScriptedLLM(LLMProvider):
    """Fake live model: proves the LLM code path (prompting, JSON parsing, retry-with-feedback)."""

    name, is_live = "scripted", True

    def __init__(self, replies):
        super().__init__(max_calls=50)
        self.replies, self.prompts = list(replies), []

    def _complete(self, system, prompt, *, max_tokens):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_llm_path_parses_and_retries_with_feedback(tmp_path, monkeypatch):
    good = json.dumps({"req_type": "greenfield", "domain": "url_shortener", "title": "Shortener",
                       "intent": "short links", "functional": ["create"], "non_functional": [],
                       "out_of_scope": [], "acceptance_criteria": ["201 on create"], "ambiguities": []})
    fake = ScriptedLLM(["not json at all", f"```json\n{good}\n```"])
    from agentforge.agents.analyst import RequirementAnalyst
    from agentforge.context import FaultInjector, RunContext
    from agentforge.guardrails import Budgets
    from agentforge.hitl import Gatekeeper
    from agentforge.models import Task
    from agentforge.orchestrator.blackboard import Blackboard
    from agentforge.orchestrator.dag import TaskGraph
    from agentforge.orchestrator.engine import Engine
    from agentforge.orchestrator.events import EventLog
    from agentforge.workspace import Workspace
    ctx = RunContext(run_dir=tmp_path, blackboard=Blackboard(), events=EventLog(None, verbose=False),
                     workspace=Workspace(tmp_path / "ws", Budgets()), llm=fake, gate=Gatekeeper(), budgets=Budgets(),
                     faults=FaultInjector())
    graph = TaskGraph([Task("analyze", "a", "analyst", params={"requirement": "shortener"}, outputs=["spec"])])
    Engine(ctx, {"analyst": RequirementAnalyst()}, base_backoff=0.01).run(graph)
    assert graph.tasks["analyze"].status == TaskStatus.DONE and graph.tasks["analyze"].attempts == 2
    assert "Your previous attempt failed" in fake.prompts[1]
    assert ctx.blackboard.get("spec")["domain"] == "url_shortener"


@pytest.mark.parametrize("name", ["greenfield"])
def test_cli_demo_entrypoint(tmp_path, name):
    proc = subprocess.run([sys.executable, "-m", "agentforge", "demo", name, "--out", str(tmp_path), "--quiet",
                           "--llm", "offline"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "success" in proc.stdout
