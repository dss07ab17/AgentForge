from pathlib import Path

import pytest

from agentforge.guardrails import (Budgets, GuardrailViolation, SandboxEscape, check_content, run_command,
                                   scan_python, scan_secrets)
from agentforge.hitl import ClarificationNeeded, Gatekeeper, GateRejected
from agentforge.models import Ambiguity, Severity
from agentforge.workspace import EditConflict, Workspace


@pytest.mark.parametrize("source,needle", [
    ("eval('1')", "eval"), ("import os\nos.system('ls')", "os.system"),
    ("import subprocess\nsubprocess.run(['ls'], shell=True)", "shell=True"),
    ("import pickle\npickle.loads(b'')", "pickle.loads"), ("import shutil\nshutil.rmtree('/')", "rmtree"),
])
def test_policy_rejects_dangerous_code(source, needle):
    assert any(needle in p for p in scan_python(source, "x.py"))


def test_policy_accepts_safe_code():
    assert scan_python("import json\nprint(json.dumps({}))\n", "x.py") == []


def test_secret_detection():
    assert scan_secrets('API_KEY = "sk-ant-abcdefghijklmnop"', "c.py")
    assert scan_secrets('password = "hunter2hunter2"', "c.py")
    assert not scan_secrets('password = os.environ["PW"]', "c.py")


def test_workspace_blocks_escapes_and_protected_paths(tmp_path):
    ws = Workspace(tmp_path / "ws", Budgets())
    for bad in ["../evil.py", "/etc/passwd", ".git/config", "a/../../x", "C:\\Windows\\x.py", "\\x.py",
                "..\\evil.py"]:
        with pytest.raises(SandboxEscape):
            ws.write(bad, "x", author="t")
    for good in ["tests/conftest.py", "tests\\unit\\a.py"]:
        assert ws.root in ws.guard.resolve(good).parents


def test_workspace_rejects_policy_violations_and_oversize(tmp_path):
    ws = Workspace(tmp_path / "ws", Budgets(max_file_bytes=100))
    with pytest.raises(GuardrailViolation):
        ws.write("a.py", "eval('2')", author="t")
    with pytest.raises(GuardrailViolation):
        check_content("b.txt", "x" * 101, Budgets(max_file_bytes=100))


def test_file_budget(tmp_path):
    ws = Workspace(tmp_path / "ws", Budgets(max_files=2))
    ws.write("a.txt", "a", author="t")
    ws.write("b.txt", "b", author="t")
    ws.write("a.txt", "a2", author="t")  # overwriting does not consume budget
    with pytest.raises(GuardrailViolation):
        ws.write("c.txt", "c", author="t")


def test_edits_are_anchored_and_atomic(tmp_path):
    ws = Workspace(tmp_path / "ws", Budgets())
    ws.write("m.py", "x = 1\ny = 1\n", author="t")
    with pytest.raises(EditConflict):  # second anchor missing -> nothing applied
        ws.apply_edits([{"file": "m.py", "old": "x = 1", "new": "x = 2"},
                        {"file": "m.py", "old": "z = 1", "new": "z = 2"}], author="t")
    assert ws.read("m.py") == "x = 1\ny = 1\n"
    with pytest.raises(EditConflict):  # ambiguous anchor
        ws.apply_edits([{"file": "m.py", "old": " = 1", "new": " = 3"}], author="t")
    ws.apply_edits([{"file": "m.py", "old": "x = 1", "new": "x = 2"}], author="t")
    assert ws.read("m.py") == "x = 2\ny = 1\n"


def test_brownfield_copy_never_touches_original(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8")
    ws = Workspace.from_repo(repo, tmp_path / "run", Budgets())
    ws.apply_edits([{"file": "a.py", "old": "A = 1", "new": "A = 2"}], author="t")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert "-A = 1\n+A = 2" in ws.diff()


def test_command_allow_list(tmp_path):
    with pytest.raises(SandboxEscape):
        run_command(["rm", "-rf", "/"], tmp_path, Budgets())


def test_gate_policy_halts_on_blocking_ambiguity():
    amb = [Ambiguity("a", "q?", "w", ["x", "y"], "x", Severity.HIGH), Ambiguity("b", "q?", "w", ["x"], "x", Severity.LOW)]
    with pytest.raises(ClarificationNeeded) as exc:
        Gatekeeper(mode="auto").clarify(amb)
    assert [q.id for q in exc.value.questions] == ["a"]
    resolved = Gatekeeper(mode="auto", accept_defaults=True).clarify(
        [Ambiguity("a", "q?", "w", ["x", "y"], "x", Severity.HIGH)])
    assert resolved[0].resolution == "x" and resolved[0].resolved_by == "default"


def test_gate_answers_file_can_reject():
    gk = Gatekeeper(mode="answers", answers={"approvals": {"release": False}})
    with pytest.raises(GateRejected):
        gk.approve("release", "summary")
    assert gk.audit[-1]["decision"] == "rejected"


def test_sample_repo_exists():
    assert (Path(__file__).parents[1] / "examples/brownfield/linkshort_v1/app/store.py").exists()
