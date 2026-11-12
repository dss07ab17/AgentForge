# AgentForge: an agentic software-engineering pipeline

AgentForge turns a software requirement into a **reviewable engineering outcome**: a normalized spec,
a task graph, generated code, API contract, schema, tests and documentation. It validates that output
by actually running it, and reports the result in a structured engineering summary. Specialist agents
work autonomously between **human approval gates**.

```
requirement ─► Analyst ─┐                                   ┌─► Coder × N ─┐
                        ├─► G1 Clarify ─► Planner ─► G2 Plan ┼─► Tester ────┼─► Validator ⇄ Repairer ─► G3 Release ─► Report
 (repo) ─► Codebase ────┘                                   ├─► Architect  │
                                                            └─► Docs, Risk ┘
```

It is not a chatbot. A requirement goes in, an orchestrated DAG of agent tasks runs with parallelism,
retries, a validate/repair loop and guardrails, and what comes out is code that has been tested, a
patch, or a precise list of questions.

## Quick start

Requires Python 3.10+, `jinja2` and `pytest`. No API key is needed.

```bash
pip install -r requirements.txt

# Mandatory use case: greenfield URL shortener
python -m agentforge demo greenfield

# All bundled scenarios: greenfield, brownfield, ambiguous (+answered), fault recovery
python -m agentforge demo all

# Your own requirement (prompts at the gates when run in a terminal)
python -m agentforge run "Build a scalable URL shortener service with APIs, persistence, and analytics."

# Brownfield: change an existing repo (it is copied, never modified; you get a patch)
python -m agentforge run "Add optional link expiration ... fix the overwrite bug" \
    --repo examples/brownfield/linkshort_v1

# Framework test suite (41 tests, ~25 s)
python -m pytest
```

Each run writes to `runs/<run-id>/`:

| file | what it is |
|---|---|
| `ENGINEERING_SUMMARY.md` | **The final output**: spec, ambiguities and resolutions, impact analysis, plan with DAG, architecture and decisions, artifacts, validation history, risks/trade-offs, audit trail, assumptions and limitations |
| `workspace/` | Generated project (greenfield) or modified copy of the repo (brownfield) |
| `changes.patch` | Brownfield only: validated diff, applies with `git apply` |
| `CLARIFICATION_REQUEST.md`, `answers.template.json` | When the run stopped at G1 |
| `summary.json`, `trace.jsonl`, `state.json`, `plan.mmd` | Machine-readable summary, event trace, resumable checkpoint, task graph |

Pre-generated outputs for every scenario are in [`examples/outputs/`](examples/outputs). The full
generated URL-shortener project is in [`examples/outputs/greenfield/generated_project/`](examples/outputs/greenfield/generated_project).

## How it maps to the assignment

| Requirement | Where |
|---|---|
| 1. Requirement understanding | `agents/analyst.py`, `knowledge/domains.py`: type/domain detection, vague-term detection, ambiguities with severity, options, safe defaults; normalized `Spec` with acceptance criteria |
| 2. Task decomposition | `agents/planner.py`: DAG with explicit dependencies; spawned into the live graph behind the plan gate |
| 3. Codebase reasoning | `agents/codebase.py`: AST analysis, import graph, entry points, data flows, layer ordering, concept-based impact scoring, risk hotspots, affected tests |
| 4. Orchestration | `orchestrator/engine.py`: parallel waves, retries with backoff and error feedback, output contracts, failure propagation, dynamic tasks + hand-off rewiring, checkpoint/resume; `orchestrator/blackboard.py` for cross-step coordination |
| 5. Engineering outputs | Code, OpenAPI contract, SQL schema, unit + integration + contract tests, docs, Dockerfile |
| 6. Validation and risk | `agents/validator.py` (syntax, policy, lint, contract, tests, regression), `agents/repairer.py`, `agents/risk.py`, `guardrails.py` |
| 7. Controlled autonomy | `hitl.py`: gates G1 clarify / G2 plan / G3 release; interactive, policy or answers-file; full audit trail |
| 8. Final output | `reporter.py` → `ENGINEERING_SUMMARY.md` |

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/SCENARIOS.md](docs/SCENARIOS.md) · [docs/TESTING.md](docs/TESTING.md)

## CLI reference

```
python -m agentforge run [REQUIREMENT] [--repo PATH] [--answers FILE] [--accept-defaults]
                         [--approval auto|interactive] [--llm auto|offline|anthropic]
                         [--inject-fault TASK:transient|bug]... [--resume RUN_DIR] [--workers N] [--out DIR]
python -m agentforge demo {greenfield,brownfield,ambiguous,ambiguous-answered,recovery,all}
```

Exit codes: `0` success · `2` failed (partial report written) · `3` needs clarification · `4` rejected at a gate.

Answering a clarification request and continuing the same run:

```bash
python -m agentforge run "We need a links thing that's fast and has some stats. Maybe users too?"   # exits 3
# edit runs/<id>/answers.template.json, then:
python -m agentforge run --resume runs/<id> --answers runs/<id>/answers.template.json
```

## LLM mode vs offline mode

Every agent has two strategies:

- **Offline (default without a key):** deterministic rules plus domain blueprints. The URL-shortener
  blueprint is *parameterized by the clarified spec*, so answers at G1 change the generated code,
  contract and schema (see `ambiguous-answered`). This makes runs reproducible and testable in CI,
  and lets a reviewer run everything with no credentials.
- **Claude (`ANTHROPIC_API_KEY` set, or `--llm anthropic`):** agents prompt the model for structured
  JSON (spec, plan, files, anchored edits, repairs, risks). Every reply goes through the same output
  contracts, guardrails, validator and gates. Invalid JSON or invalid output is retried with the error
  fed back. Set the model with `AGENTFORGE_MODEL`.

Both modes share the orchestration, guardrails, validation and reporting, and those parts are the
core of the system. The LLM path is covered by tests that use a scripted fake model. It has **not**
been exercised against the live API in this deliverable (see limitations).

## Known limitations

- Offline generation covers the URL-shortener domain and the bundled brownfield sample. Other domains
  or repositories need LLM mode, and fail cleanly with a report otherwise.
- The generated service uses stdlib WSGI and SQLite with an in-process cache and queue, so anyone can
  run it. The scaling path to PostgreSQL, Redis and Kafka is documented but not generated.
- Codebase analysis is Python-only (AST based).
- The validator proves that the generated tests pass and that the contract matches. It cannot prove
  the tests are exhaustive, which is why G3 is a human gate.
- No load testing. Performance numbers are design targets.

## Project layout

```
agentforge/
  orchestrator/   dag.py (graph), engine.py (execution), blackboard.py (shared state), events.py (trace)
  agents/         analyst, clarifier, codebase, planner, architect, coder, tester, docs, validator,
                  repairer, risk, gate
  knowledge/      domains.py (analysis rules), url_shortener/ (blueprint + templates), brownfield/ (recipe)
  llm/            provider interface, offline provider, Anthropic provider (stdlib HTTPS)
  guardrails.py   sandbox, code policy, command allow-list, budgets
  hitl.py         approval gates + audit
  workspace.py    sandboxed files, anchored edits, diffs
  workflow.py     run setup, initial graph, finalize; cli.py
examples/         brownfield sample repo, answers file, pre-generated outputs
tests/            engine, guardrails, analysis, end-to-end scenarios, LLM path
```
