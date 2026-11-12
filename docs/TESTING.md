# Testing approach

Two different things need to be trusted: **the pipeline itself** and **what the pipeline produces**.

## 1. Validating generated output (every run)

The validator agent is deterministic in every mode. LLMs generate, and tools verify. Each iteration
re-checks the whole workspace:

| Check | What it proves | Blocking |
|---|---|---|
| syntax | every `.py` parses | yes |
| policy | no forbidden constructs (eval/exec/shell/unsafe deserialisation/rmtree) or committed secrets; defence in depth on top of the per-write scan | yes |
| lint | `ruff --select F,E9`: undefined names, unused imports, syntax issues | yes (skipped if ruff is absent) |
| contract | `ROUTES` in code == operations in the published contract == `api/openapi.json` on disk | yes |
| tests | full pytest run in a sandboxed subprocess (allow-listed, timeout, scrubbed env), parsed from JUnit XML per test | yes |
| regression | brownfield: every test that passed on the untouched baseline still passes | yes |

In addition, the generated suite itself contains:
- **unit tests** for every module, with injected clocks and random generators (deterministic expiry, TTL
  cache, token bucket, collision retry)
- **integration tests** through the WSGI app (HTTP semantics, error shape, 404/405/409/410/413/429,
  request-id propagation, no internal leakage on 500)
- **a live threaded HTTP server test** with concurrent creates and redirects that checks unique codes
  and exact click totals
- **contract tests** that compare routes and response bodies against the OpenAPI schemas
- **contract coverage**, computed by the tester: share of documented operations exercised by tests
  (100% for the URL shortener)

Failures enter a bounded repair loop (max 3 iterations), and the release gate waits for the final verdict.
The repairer is forbidden from editing tests or the contract.

## 2. Validating the pipeline (`python -m pytest`, 41 tests)

| File | Covers |
|---|---|
| `tests/test_engine.py` | cycle/unknown-dep detection; real parallelism (≥3 concurrent); retry with backoff and error feedback; non-retriable failures; transitive blocking while independent branches finish; non-critical tasks; output contracts; dynamic spawn + hand-off rewiring order; blackboard attribution; checkpointing |
| `tests/test_guardrails_and_workspace.py` | each forbidden construct; secret detection; sandbox escapes (`..`, absolute, `.git`); size and file budgets; atomic anchored edits (missing and ambiguous anchors); original repo untouched; command allow-list; gate policy halts on HIGH; rejection via answers file |
| `tests/test_analysis.py` | type/domain detection; clear vs vague requirements produce different blocking sets; impact analysis (modules, layer order, entry point, flows, hotspots, affected tests); route extraction == contract; options change the contract |
| `tests/test_scenarios.py` | **end-to-end** greenfield (and the generated project's tests pass independently), brownfield (regression gate, patch passes `git apply --check`, original unchanged), ambiguous halt, resume with answers (generated code reflects the answers), fault recovery (retry + repair loop + rewiring), rejected gate, unrecoverable domain (failed run still reports, release blocked), runs nested inside another pytest project, **LLM path** with a scripted model (invalid JSON → retry with feedback → parsed spec), CLI entry point |

The URL-shortener blueprint was also checked across **all 16 combinations** of its options
(alias × expiry × analytics depth × rate limit). Every combination renders, lints clean and passes its
own generated tests.

## 3. Known limitations and trade-offs

- **The tests are the oracle.** Passing validation means the code satisfies the generated tests and
  the contract. It does not prove the tests are complete. Mitigations: tests come from the design and
  contract (not the code), contract coverage is measured, and a human approves at G3.
- **Offline mode is blueprint-driven.** It shows the orchestration, validation and gates
  deterministically, but its "generation" for the URL shortener is template rendering parameterized
  by the spec, and its brownfield edits come from a recipe for the bundled sample. Arbitrary
  domains and repos require LLM mode.
- **The LLM path is tested with a scripted fake model**, not the live API, in this deliverable. The
  shared machinery (contracts, guardrails, validator, gates) is the same code in both modes.
- **Offline repair regenerates whole components**. LLM repair produces targeted edits.
- **No performance testing.** Scale figures are design targets backed by the architecture, not
  measurements.
- **Python-only codebase analysis.**
