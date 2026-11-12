# Example scenarios

All of these run offline with `python -m agentforge demo <name>`. The full outputs are committed under
[`examples/outputs/<name>/`](../examples/outputs).

| Scenario | Input | Outcome | Highlights |
|---|---|---|---|
| `greenfield` | "Build a scalable URL shortener service with APIs, persistence, and analytics." | ✅ success, 82/82 generated tests pass | Mandatory use case; 5 ambiguities resolved by safe defaults; 14-task DAG |
| `brownfield` | Add link expiry (410 for expired) + fix an overwrite bug, on `examples/brownfield/linkshort_v1` | ✅ success, 17/17 tests (3 baseline + 14 new), 0 regressions | Impact analysis, anchored edits, migration, patch export |
| `ambiguous` | "We need a links thing that's fast and has some stats. Maybe users too?" | ⏸ needs clarification (exit 3) | Stops at G1 with 3 blocking questions instead of guessing |
| `ambiguous-answered` | same, plus `examples/answers/ambiguous_answers.json` | ✅ success, 61/61 tests | Answers change the generated contract, schema and code |
| `recovery` | greenfield + injected faults | ✅ success after 1 retry + 1 repair loop | Error handling and recovery |

---

## 1. Greenfield: the mandatory URL shortener

**Input:** `Build a scalable URL shortener service with APIs, persistence, and analytics.`

**Requirement understanding.** The analyst classifies the request as greenfield, domain
`url_shortener`, and flags "scalable" as vague. It produces 5 functional requirements, 6 NFRs (including the resolved scale target),
3 out-of-scope items and 5 testable acceptance criteria. No question is blocking, so the policy applies
documented defaults:

| ambiguity | severity | resolution |
|---|---|---|
| scale target | medium | 1k creates/s, 50k redirects/s peak; 100M links/yr |
| analytics depth | medium | detailed: totals, per day, top referrers |
| custom alias | low | yes |
| expiry | low | yes |
| rate limiting | medium | yes |

**Task decomposition and orchestration.** 14 engineering tasks behind G2. The tests, contract, schema,
risks and core code start in the same wave, as soon as the architecture exists:

```
{design_architecture}
 → {analyze_risks, define_api_contract, define_data_schema, impl_core, write_unit_tests}
 → {impl_storage, write_docs, write_integration_tests}
 → {impl_analytics} → {impl_service} → {impl_api} → {validate} → {approve_release}
```

**Generated artifacts** ([browse](../examples/outputs/greenfield/generated_project)):
- `shortener/`: config, base62 codec, random code generator, validation, models, error model,
  SQLite repository (WAL, per-thread connections), TTL+LRU cache, async batched click recorder,
  token-bucket rate limiter, service layer, WSGI API, threaded dev server
- `api/openapi.json`: OpenAPI 3.0 contract (6 operations, request/response schemas, error model)
- `shortener/storage/schema.sql`: `links` + `click_events` with indexes
- `tests/`: 7 unit modules and 3 integration modules: an in-process WSGI client, **a real threaded HTTP
  server with 40 concurrent creates and 120 concurrent redirects**, and contract tests that check routes
  and response bodies against the OpenAPI schemas
- `docs/README.md`, `docs/ARCHITECTURE.md`, `Dockerfile`, `pyproject.toml`

**Validation:** syntax ✔ · policy ✔ · ruff ✔ · contract (6 routes = 6 operations) ✔ · tests 82/82 ✔.
Contract coverage by tests: 100%.

**Risks and trade-offs** (excerpt): open write API (high, auth is out of scope), SQLite contention at
the target scale (mitigation: repository protocol, PostgreSQL path), analytics loss window on crash,
per-instance cache staleness; 302 vs 301; async analytics vs consistency.

## 2. Brownfield: enhancement + bug fix on an existing repo

**Input:** *Add optional link expiration: clients can pass ttl_seconds when shortening and expired links
must return HTTP 410. Also fix the bug where a newly generated code can silently overwrite an existing
link.* `--repo examples/brownfield/linkshort_v1`

**Codebase reasoning** (static, AST-based):
- Entry point `app/web.py`; data flow `app/web.py -> app/service.py -> app/store.py`; table `links`; API `/shorten`
- Impacted modules in implementation order (data layer first): `codes.py` (medium), `db.py`, `store.py`, `service.py`, `web.py`
- Hotspots found: **`app/store.py:7 INSERT OR REPLACE silently overwrites existing rows`** (the bug's
  root cause), a shared SQLite connection, and module-level global state
- Tests affected: `tests/test_service.py`

**Change design:** additive nullable `expires_at` plus an idempotent migration; strict `INSERT` with
`DuplicateCode`; bounded retry on collision; TTL validation; 410 for expired; `get_url` kept for
backward compatibility.

**Orchestration:** `baseline_tests` (existing suite on the untouched copy) runs in parallel with the
per-module coder tasks, which are ordered by the import graph (`store` → `service` → `web`). The
`codes.py` task concludes "no change needed".

**Validation:** 17/17 tests pass. The **regression check** confirms that all 3 baseline tests still pass.
New tests include a regression test that reproduces the overwrite bug, TTL boundary tests, an HTTP 410
test, and a migration test on a v1-schema database.

**Output:** [`changes.patch`](../examples/outputs/brownfield/changes.patch). It applies cleanly with
`git apply`, and the original repo is untouched (both are verified in the test suite).

## 3. Ambiguous requirement

**Input:** `We need a links thing that's fast and has some stats. Maybe users too?`

The analyst detects the vague terms *thing, fast, some, maybe, users* and raises 8 questions, 3 of them
**blocking**:

| id | question | why it blocks |
|---|---|---|
| system | Is this a URL shortener? | "links thing" could be a bookmark manager |
| analytics | Which analytics are required? | "some stats" decides the event model and storage growth |
| users | Are user accounts in scope? | Roughly doubles scope (identity, authz, ownership) |

Under the auto policy the run **stops at G1** (exit 3). It writes `CLARIFICATION_REQUEST.md` and an
`answers.template.json`, and plans nothing.

**`ambiguous-answered`**: the same requirement with the
[answers file](../examples/answers/ambiguous_answers.json) (basic analytics, no aliases, no expiry,
accounts deferred). The spec records each decision as *decided by human*. The generated system really
differs: the contract has no `custom_alias`/`expires_in_seconds` and no 410, the schema has no
`click_events` table, and stats are totals only. 61/61 tests pass.

The same flow works on a stopped run: `--resume runs/<id> --answers ...` continues from the
checkpoint.

## 4. Recovery: error handling demonstration

`--inject-fault impl_storage:transient --inject-fault impl_api:bug`

1. `impl_storage` fails with a simulated LLM timeout, is retried after 0.2 s backoff, and succeeds on
   attempt 2.
2. `impl_api` returns code with a planted defect (redirect returns `200` instead of `302`). The coder's
   contract check passes (routes are correct), so this is a behavioural bug.
3. `validate` fails: 79/82, with 3 failing tests named in the report.
4. The validator spawns `repair_1 → validate_2` and **rewires `approve_release` to wait on
   `validate_2`**.
5. The repairer maps the failing tests to components, regenerates them, and finds that only
   `shortener/api.py` changed. Tests are protected and untouched.
6. `validate_2` passes 82/82, and G3 approves.

The report's "Error handling and recovery events" section and the task table (attempts, `spawned_by`)
show all of this.
