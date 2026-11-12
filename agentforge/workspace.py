"""Sandboxed workspace where all generated or changed files live.

For brownfield runs the target repository is *copied* in and a pristine
baseline is kept alongside, so every change can be shown as a unified diff and
exported as a patch that a human reviews and applies.
"""
from __future__ import annotations

import difflib
import hashlib
import shutil
import threading
from pathlib import Path

from .guardrails import Budgets, GuardrailViolation, PathGuard, check_content

_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv", "venv", "*.pyc", "*.db", "node_modules")


def _lines(path: Path) -> list[str]:
    """Lines for a git-compatible diff (marks a missing final newline)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


class EditConflict(RuntimeError):
    """A targeted edit's anchor text was not found exactly once (retriable)."""


class Workspace:
    def __init__(self, root: Path, budgets: Budgets, baseline: Path | None = None) -> None:
        self.root = root.resolve()
        self.baseline = baseline.resolve() if baseline else None
        self.budgets = budgets
        self.guard = PathGuard(root)
        self.manifest: dict[str, dict] = {}
        self._lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_repo(cls, repo: Path, run_dir: Path, budgets: Budgets) -> "Workspace":
        root, baseline = run_dir / "workspace", run_dir / "baseline"
        for target in (root, baseline):
            if not target.exists():
                shutil.copytree(repo, target, ignore=_IGNORE)
        return cls(root, budgets, baseline=baseline)

    # ------------------------------------------------------------------ io
    def read(self, rel_path: str) -> str:
        return self.guard.resolve(rel_path).read_text(encoding="utf-8")

    def exists(self, rel_path: str) -> bool:
        return self.guard.resolve(rel_path).exists()

    def list_files(self, suffix: str = "") -> list[str]:
        out = []
        for p in sorted(self.root.rglob("*")):
            parts = p.relative_to(self.root).parts
            if p.is_file() and not any(d.startswith(".") or d == "__pycache__" for d in parts[:-1]):
                rel = p.relative_to(self.root).as_posix()
                if rel.endswith(suffix):
                    out.append(rel)
        return out

    def write(self, rel_path: str, content: str, *, author: str) -> None:
        target = self.guard.resolve(rel_path)
        check_content(rel_path, content, self.budgets)
        with self._lock:
            if rel_path not in self.manifest and len(self.manifest) >= self.budgets.max_files:
                raise GuardrailViolation(f"file budget exceeded ({self.budgets.max_files} files)")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            self.manifest[rel_path] = {
                "author": author,
                "bytes": len(content.encode()),
                "sha256": hashlib.sha256(content.encode()).hexdigest()[:12],
            }

    def apply_edits(self, edits: list[dict[str, str]], *, author: str) -> None:
        """Apply search/replace edits atomically: all anchors are checked first."""
        staged: dict[str, str] = {}
        for edit in edits:
            path = edit["file"]
            if edit.get("create"):
                staged[path] = edit["new"]
                continue
            current = staged.get(path) if path in staged else self.read(path)
            count = current.count(edit["old"])
            if count != 1:
                raise EditConflict(
                    f"{path}: edit anchor found {count} times (expected exactly 1): {edit['old'][:60]!r}")
            staged[path] = current.replace(edit["old"], edit["new"], 1)
        for path, content in staged.items():
            self.write(path, content, author=author)

    # ---------------------------------------------------------------- diffs
    def diff(self) -> str:
        if not self.baseline:
            return ""
        chunks = []
        files: set[str] = set()
        for base in (self.root, self.baseline):
            for p in base.rglob("*"):
                parts = p.relative_to(base).parts
                if p.is_file() and not any(d.startswith(".") or d == "__pycache__" for d in parts[:-1]) \
                        and not p.name.endswith((".db", ".pyc", ".db-wal", ".db-shm")):
                    files.add(p.relative_to(base).as_posix())
        for rel in sorted(files):
            a, b = self.baseline / rel, self.root / rel
            old = _lines(a) if a.exists() else []
            new = _lines(b) if b.exists() else []
            if old != new:
                chunks.extend(difflib.unified_diff(
                    old, new, fromfile=f"a/{rel}" if a.exists() else "/dev/null",
                    tofile=f"b/{rel}" if b.exists() else "/dev/null"))
        return "".join(chunks)

    def changed_files(self) -> list[str]:
        return sorted(self.manifest)
