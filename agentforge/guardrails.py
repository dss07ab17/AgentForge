"""Guardrails that keep autonomous agents safe and their output reviewable.

Layers:
1. Filesystem sandbox  - agents can only write inside the run workspace; the
   user's original repository is never modified (brownfield work happens on a
   copy and comes back as a patch for a human to apply).
2. Generated-code policy - AST scan that rejects dangerous constructs
   (eval/exec, shelling out, unsafe deserialisation, recursive deletes) and
   hard-coded secrets before a file is accepted.
3. Execution sandbox - only allow-listed commands run, with a timeout, inside
   the workspace, with credentials scrubbed from the environment.
4. Budgets - caps on files, file size, repair iterations and LLM calls so a
   misbehaving loop cannot run away.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .orchestrator.engine import NonRetriableError


class GuardrailViolation(RuntimeError):
    """Retriable: an agent can be asked to regenerate compliant output."""


class SandboxEscape(NonRetriableError):
    """Never retried: an attempt to touch something outside the sandbox."""


@dataclass
class Budgets:
    max_files: int = 200
    max_file_bytes: int = 200_000
    max_repair_iterations: int = 3
    max_llm_calls: int = 120
    command_timeout_s: int = 180


_FORBIDDEN_CALLS = {
    "eval": "dynamic code execution",
    "exec": "dynamic code execution",
    "__import__": "dynamic import",
    "os.system": "shell execution",
    "os.popen": "shell execution",
    "os.execv": "process replacement",
    "subprocess.run": "process execution",
    "subprocess.Popen": "process execution",
    "subprocess.call": "process execution",
    "subprocess.check_output": "process execution",
    "subprocess.check_call": "process execution",
    "pickle.loads": "unsafe deserialisation",
    "pickle.load": "unsafe deserialisation",
    "marshal.loads": "unsafe deserialisation",
    "shutil.rmtree": "recursive delete",
    "yaml.load": "unsafe YAML load (use safe_load)",
}

_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "Anthropic API key"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "API secret key"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"""(?i)(password|passwd|secret|api_key)\s*=\s*["'][^"'\s]{8,}["']"""), "hard-coded credential"),
]

_PROTECTED_PARTS = {".git", ".env", ".ssh"}


def _call_name(node: ast.Call) -> str:
    fn = node.func
    parts: list[str] = []
    while isinstance(fn, ast.Attribute):
        parts.append(fn.attr)
        fn = fn.value
    if isinstance(fn, ast.Name):
        parts.append(fn.id)
    return ".".join(reversed(parts))


def scan_python(source: str, filename: str) -> list[str]:
    """Return a list of policy violations for a Python source file."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"{filename}:{exc.lineno}: syntax error: {exc.msg}"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _FORBIDDEN_CALLS:
                problems.append(f"{filename}:{node.lineno}: forbidden call {name}() - {_FORBIDDEN_CALLS[name]}")
            if name.startswith("subprocess.") or name == "Popen":
                for kw in node.keywords:
                    if kw.arg == "shell":
                        problems.append(f"{filename}:{node.lineno}: shell=True is not allowed")
    return problems


def scan_secrets(source: str, filename: str) -> list[str]:
    problems = []
    for lineno, line in enumerate(source.splitlines(), 1):
        for pattern, label in _SECRET_PATTERNS:
            if pattern.search(line):
                problems.append(f"{filename}:{lineno}: possible {label} committed in source")
    return problems


class PathGuard:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, rel_path: str) -> Path:
        # The check is purely lexical: calling Path.resolve() on not-yet-existing paths races with other agent
        # threads creating the same directories (on Windows a safe path could then look like an escape). This is
        # sound because no symlinks exist in the workspace - copytree copies files and doesn't follow links into it.
        if (not rel_path or os.path.isabs(rel_path) or PureWindowsPath(rel_path).drive
                or rel_path.startswith(("/", "\\"))):
            raise SandboxEscape(f"absolute or empty path rejected: {rel_path!r}")
        normalized = os.path.normpath(rel_path)
        if normalized == ".." or Path(normalized).parts[:1] == ("..",):
            raise SandboxEscape(f"path escapes the workspace sandbox: {rel_path!r}")
        if _PROTECTED_PARTS.intersection(Path(rel_path).parts):
            raise SandboxEscape(f"path touches a protected location: {rel_path!r}")
        return self.root / normalized


def check_content(rel_path: str, content: str, budgets: Budgets) -> None:
    if len(content.encode("utf-8")) > budgets.max_file_bytes:
        raise GuardrailViolation(f"{rel_path}: exceeds max file size of {budgets.max_file_bytes} bytes")
    problems = scan_secrets(content, rel_path)
    if rel_path.endswith(".py"):
        problems += scan_python(content, rel_path)
    if problems:
        raise GuardrailViolation("generated code rejected by policy:\n  " + "\n  ".join(problems))


_ALLOWED_COMMANDS = [
    [sys.executable, "-m", "pytest"],
    [sys.executable, "-m", "compileall"],
    [sys.executable, "-I", "-c"],
    ["ruff", "check"],
]
_SECRET_ENV = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)")


def run_command(cmd: list[str], cwd: Path, budgets: Budgets) -> subprocess.CompletedProcess:
    """Run an allow-listed command inside the sandbox with a scrubbed environment."""
    if not any(cmd[: len(prefix)] == prefix for prefix in _ALLOWED_COMMANDS):
        raise SandboxEscape(f"command not on the allow-list: {' '.join(cmd[:3])} ...")
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(cwd)
    return subprocess.run(  # noqa: S603 - allow-listed, no shell
        cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=budgets.command_timeout_s,
    )
