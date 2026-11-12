"""Command-line interface.

    python -m agentforge run "Build a scalable URL shortener service with APIs, persistence, and analytics."
    python -m agentforge run "Add link expiry ..." --repo examples/brownfield/linkshort_v1
    python -m agentforge demo all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import workflow

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = {
    "greenfield": {
        "requirement": "Build a scalable URL shortener service with APIs, persistence, and analytics.",
    },
    "brownfield": {
        "requirement": "Add optional link expiration: clients can pass ttl_seconds when shortening and expired "
                       "links must return HTTP 410. Also fix the bug where a newly generated code can silently "
                       "overwrite an existing link.",
        "repo": ROOT / "examples/brownfield/linkshort_v1",
    },
    "ambiguous": {
        "requirement": "We need a links thing that's fast and has some stats. Maybe users too?",
    },
    "ambiguous-answered": {
        "requirement": "We need a links thing that's fast and has some stats. Maybe users too?",
        "answers": ROOT / "examples/answers/ambiguous_answers.json",
    },
    "recovery": {
        "requirement": "Build a scalable URL shortener service with APIs, persistence, and analytics.",
        "faults": ["impl_storage:transient", "impl_api:bug"],
    },
}


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", type=Path, default=Path("runs"), help="directory for run outputs (default: runs/)")
    p.add_argument("--llm", choices=["auto", "offline", "anthropic"], default="auto",
                   help="auto = Claude if ANTHROPIC_API_KEY is set, else deterministic offline mode")
    p.add_argument("--approval", choices=["auto", "interactive"], default=None,
                   help="interactive prompts at gates (default when attached to a terminal); auto = policy")
    p.add_argument("--workers", type=int, default=4, help="max tasks executed in parallel")
    p.add_argument("--quiet", action="store_true")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="agentforge", description="Agentic software-engineering pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the pipeline on a requirement")
    r.add_argument("requirement", nargs="?", help="the requirement text (omit with --resume)")
    r.add_argument("--repo", type=Path, help="existing repository (brownfield); it is copied, never modified")
    r.add_argument("--answers", type=Path, help="JSON with clarification answers / approvals")
    r.add_argument("--accept-defaults", action="store_true", help="resolve blocking ambiguities with defaults")
    r.add_argument("--inject-fault", action="append", metavar="TASK:KIND",
                   help="deterministic fault for demos: KIND = transient | bug (repeatable)")
    r.add_argument("--resume", type=Path, help="resume a previous run directory from its checkpoint")
    _common(r)

    d = sub.add_parser("demo", help="run a bundled example scenario")
    d.add_argument("scenario", choices=[*SCENARIOS, "all"])
    _common(d)

    args = parser.parse_args(argv)
    approval = args.approval or ("interactive" if sys.stdin.isatty() and args.cmd == "run" else "auto")

    if args.cmd == "run":
        if not args.requirement and not args.resume:
            parser.error("a requirement is required unless --resume is given")
        outcome = workflow.run(args.requirement, repo=args.repo, out_dir=args.out, llm=args.llm, approval=approval,
                               answers=args.answers, accept_defaults=args.accept_defaults, faults=args.inject_fault,
                               max_workers=args.workers, resume=args.resume, verbose=not args.quiet)
        print(f"\n{outcome.status.upper()}: report -> {outcome.report}")
        return outcome.exit_code

    names = [n for n in SCENARIOS] if args.scenario == "all" else [args.scenario]
    results = {}
    for name in names:
        sc = SCENARIOS[name]
        print(f"\n===== scenario: {name} =====", file=sys.stderr)
        outcome = workflow.run(sc["requirement"], repo=sc.get("repo"), out_dir=args.out, llm=args.llm,
                               approval=approval, answers=sc.get("answers"), faults=sc.get("faults"),
                               max_workers=args.workers, verbose=not args.quiet, run_name=name)
        results[name] = outcome
    print("\nscenario results:")
    for name, outcome in results.items():
        print(f"  {name:20s} {outcome.status:20s} {outcome.report}")
    expected = {"ambiguous": "needs_clarification"}
    ok = all(o.status == expected.get(n, "success") for n, o in results.items())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
