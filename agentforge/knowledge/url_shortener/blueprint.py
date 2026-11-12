"""Domain blueprint for the URL-shortener use case.

In offline mode the Architect, Coder, Tester and Docs agents use this
blueprint as their "expert knowledge": it is *parameterized by the resolved
spec options* (custom aliases, expiry, analytics depth, rate limiting), so the
answers given at the clarification gate really change what gets generated.
In LLM mode the same agents generate content with Claude and the blueprint is
used only as a reference example in prompts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = Path(__file__).with_name("templates")
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True,
                   trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined)

DEFAULT_OPTIONS: dict[str, Any] = {
    "custom_alias": True,
    "expiry": True,
    "analytics": "detailed",   # basic | detailed
    "rate_limit": True,
    "scale_target": "1k creates/s, 50k redirects/s at peak (design target); 100M links/year",
}

COMPONENTS: dict[str, list[str]] = {
    "core": ["shortener/__init__.py", "shortener/config.py", "shortener/codec.py", "shortener/idgen.py",
             "shortener/validation.py", "shortener/models.py", "shortener/errors.py"],
    "storage": ["shortener/storage/__init__.py", "shortener/storage/sqlite_repo.py", "shortener/cache.py"],
    "analytics": ["shortener/analytics.py"],
    "service": ["shortener/service.py", "shortener/ratelimit.py"],
    "api": ["shortener/api.py", "shortener/server.py"],
    "unit_tests": ["tests/conftest.py", "tests/unit/test_codec_and_idgen.py", "tests/unit/test_validation.py",
                   "tests/unit/test_cache.py", "tests/unit/test_service.py", "tests/unit/test_analytics.py",
                   "tests/unit/test_ratelimit.py"],
    "integration_tests": ["tests/integration/test_api.py", "tests/integration/test_http_server.py",
                          "tests/integration/test_contract.py"],
}
_OPTIONAL = {"shortener/ratelimit.py": "rate_limit", "tests/unit/test_ratelimit.py": "rate_limit"}

# file -> component, used by the repairer to map a failing file back to its owner
OWNER = {path: comp for comp, paths in COMPONENTS.items() for path in paths}


def normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_OPTIONS, **(options or {})}
    merged["analytics"] = "basic" if str(merged["analytics"]).lower().startswith("basic") else "detailed"
    for key in ("custom_alias", "expiry", "rate_limit"):
        merged[key] = _truthy(merged[key])
    return merged


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "y", "1", "on", "include", "enabled"} or \
        str(value).lower().startswith("yes")


def render(component: str, options: dict[str, Any]) -> dict[str, str]:
    o = normalize_options(options)
    files = {}
    for path in COMPONENTS[component]:
        flag = _OPTIONAL.get(path)
        if flag and not o[flag]:
            continue
        files[path] = _env.get_template(path + ".j2").render(o=o)
    if component == "core":
        files.update(_project_files(o))
    return files


def render_file(path: str, options: dict[str, Any]) -> str:
    return _env.get_template(path + ".j2").render(o=normalize_options(options))


def schema_sql(options: dict[str, Any]) -> str:
    return _env.get_template("shortener/storage/schema.sql.j2").render(o=normalize_options(options))


def _project_files(o: dict[str, Any]) -> dict[str, str]:
    return {
        "pyproject.toml": (
            '[project]\nname = "url-shortener"\nversion = "1.0.0"\nrequires-python = ">=3.10"\n'
            'dependencies = []  # standard library only\n\n[project.optional-dependencies]\n'
            'dev = ["pytest>=7"]\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
            'addopts = "-q"\n'
        ),
        "Dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY shortener ./shortener\n"
            "ENV SHORTENER_DATABASE_PATH=/data/shortener.db\nVOLUME /data\nEXPOSE 8080\n"
            "USER 65534\nCMD [\"python\", \"-m\", \"shortener.server\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        ),
        ".gitignore": "__pycache__/\n*.db\n*.db-wal\n*.db-shm\n.pytest_cache/\n",
    }


# --------------------------------------------------------------- contract
def openapi(options: dict[str, Any]) -> dict[str, Any]:
    o = normalize_options(options)
    err = {"$ref": "#/components/schemas/Error"}

    def resp(desc: str, schema: dict | None = None, headers: dict | None = None) -> dict:
        r: dict[str, Any] = {"description": desc}
        if schema:
            r["content"] = {"application/json": {"schema": schema}}
        if headers:
            r["headers"] = headers
        return r

    code_param = {"name": "code", "in": "path", "required": True,
                  "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}}
    create_props: dict[str, Any] = {"url": {"type": "string", "format": "uri", "maxLength": 2048}}
    if o["custom_alias"]:
        create_props["custom_alias"] = {"type": "string", "pattern": "^[A-Za-z0-9_-]{3,32}$"}
    if o["expiry"]:
        create_props["expires_in_seconds"] = {"type": "integer", "minimum": 60, "maximum": 315360000}
    link_props: dict[str, Any] = {
        "code": {"type": "string"}, "short_url": {"type": "string", "format": "uri"},
        "target_url": {"type": "string", "format": "uri"}, "created_at": {"type": "string", "format": "date-time"},
        "is_custom": {"type": "boolean"},
    }
    if o["expiry"]:
        link_props["expires_at"] = {"type": "string", "format": "date-time", "nullable": True}
    stats_props: dict[str, Any] = {
        "code": {"type": "string"}, "total_clicks": {"type": "integer"},
        "last_click_at": {"type": "string", "format": "date-time", "nullable": True},
        "pending_events": {"type": "integer", "description": "clicks accepted but not yet aggregated"},
    }
    stats_required = ["code", "total_clicks", "pending_events"]
    if o["analytics"] == "detailed":
        stats_props["clicks_by_day"] = {"type": "array", "items": {"type": "object", "properties": {
            "day": {"type": "string", "format": "date"}, "clicks": {"type": "integer"}}}}
        stats_props["top_referrers"] = {"type": "array", "items": {"type": "object", "properties": {
            "referrer": {"type": "string"}, "clicks": {"type": "integer"}}}}
        stats_required += ["clicks_by_day", "top_referrers"]

    create_responses = {
        "201": resp("Link created", {"$ref": "#/components/schemas/Link"},
                    {"Location": {"schema": {"type": "string"}}}),
        "400": resp("Invalid request", err),
        "413": resp("Request body too large", err),
        "503": resp("Could not allocate a code", err),
    }
    if o["custom_alias"]:
        create_responses["409"] = resp("Alias already taken", err)
    if o["rate_limit"]:
        create_responses["429"] = resp("Rate limit exceeded", err, {"Retry-After": {"schema": {"type": "integer"}}})
    redirect_responses = {
        "302": resp("Redirect to the target URL", headers={"Location": {"schema": {"type": "string"}}}),
        "404": resp("Unknown code", err),
    }
    if o["expiry"]:
        redirect_responses["410"] = resp("Link expired", err)

    return {
        "openapi": "3.0.3",
        "info": {"title": "URL Shortener API", "version": "1.0.0",
                 "description": "Create short links, redirect, and read click analytics."},
        "paths": {
            "/healthz": {"get": {"operationId": "health", "summary": "Liveness/readiness probe",
                                 "responses": {"200": resp("Healthy", {"type": "object", "required": ["status"],
                                               "properties": {"status": {"type": "string"}}})}}},
            "/api/v1/links": {"post": {
                "operationId": "createLink", "summary": "Create a short link",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "$ref": "#/components/schemas/CreateLinkRequest"}}}},
                "responses": create_responses}},
            "/api/v1/links/{code}": {
                "get": {"operationId": "getLink", "summary": "Get link metadata", "parameters": [code_param],
                        "responses": {"200": resp("Link", {"$ref": "#/components/schemas/Link"}),
                                      "404": resp("Unknown code", err)}},
                "delete": {"operationId": "deleteLink", "summary": "Delete a link", "parameters": [code_param],
                           "responses": {"204": resp("Deleted"), "404": resp("Unknown code", err)}}},
            "/api/v1/links/{code}/stats": {"get": {
                "operationId": "getLinkStats", "summary": "Click analytics for a link", "parameters": [code_param],
                "responses": {"200": resp("Stats", {"$ref": "#/components/schemas/LinkStats"}),
                              "404": resp("Unknown code", err)}}},
            "/{code}": {"get": {"operationId": "redirect", "summary": "Redirect to the target URL",
                                "parameters": [code_param], "responses": redirect_responses}},
        },
        "components": {"schemas": {
            "CreateLinkRequest": {"type": "object", "required": ["url"], "properties": create_props,
                                  "additionalProperties": False},
            "Link": {"type": "object", "required": ["code", "short_url", "target_url", "created_at", "is_custom"],
                     "properties": link_props},
            "LinkStats": {"type": "object", "required": stats_required, "properties": stats_props},
            "Error": {"type": "object", "required": ["error"], "properties": {"error": {
                "type": "object", "required": ["code", "message"],
                "properties": {"code": {"type": "string"}, "message": {"type": "string"}}}}},
        }},
    }


# ------------------------------------------------------------ architecture
def architecture(options: dict[str, Any]) -> dict[str, Any]:
    o = normalize_options(options)
    components = [
        {"name": "API layer (WSGI)", "module": "shortener/api.py",
         "responsibility": "routing, JSON (de)serialisation, error mapping, request ids"
                           + (", per-client rate limiting" if o["rate_limit"] else "")},
        {"name": "ShortenerService", "module": "shortener/service.py",
         "responsibility": "business rules: validation, code allocation with collision retry"
                           + (", custom aliases" if o["custom_alias"] else "")
                           + (", expiry" if o["expiry"] else "") + ", redirect resolution"},
        {"name": "Code generator", "module": "shortener/idgen.py",
         "responsibility": "random base62 codes (non-enumerable, coordination-free)"},
        {"name": "Repository (SQLite)", "module": "shortener/storage/sqlite_repo.py",
         "responsibility": "persistence behind a LinkRepository protocol; swappable for PostgreSQL"},
        {"name": "Redirect cache", "module": "shortener/cache.py",
         "responsibility": "TTL+LRU cache for the read-heavy redirect path"},
        {"name": "Click recorder", "module": "shortener/analytics.py",
         "responsibility": "async, batched, load-shedding click ingestion off the hot path"},
    ]
    decisions = [
        {"decision": "Random 7-char base62 codes + unique constraint + retry",
         "alternatives": "global counter + base62; hash(url) truncation",
         "rationale": "No single coordinator, codes are not guessable, collisions are ~1e-12 per insert at 1M links "
                      "and handled by retry. Counter needs a sequence service; hashing makes duplicates/collisions "
                      "deterministic."},
        {"decision": "Analytics written asynchronously in batches",
         "alternatives": "synchronous insert per redirect; external queue (Kafka) + stream processor",
         "rationale": "Keeps redirect latency independent of write load. In-process queue is the simplest step; "
                      "the ClickRecorder interface is where a Kafka/Kinesis producer plugs in at scale."},
        {"decision": "302 (temporary) redirects with Cache-Control: private, max-age=0",
         "alternatives": "301 permanent redirects",
         "rationale": "301 is cached by browsers so later clicks are never counted and links cannot be "
                      "re-pointed or expired; 302 preserves analytics accuracy at the cost of extra requests."},
        {"decision": "Standard-library WSGI + SQLite for the prototype",
         "alternatives": "FastAPI + PostgreSQL + Redis",
         "rationale": "Zero dependencies so reviewers can run it anywhere. Layering (API -> service -> "
                      "repository protocol) keeps the swap to Postgres/Redis local to two modules."},
        {"decision": "Denormalized click_count on links" + (" plus raw click_events" if o["analytics"] == "detailed" else ""),
         "alternatives": "aggregate on read only",
         "rationale": "O(1) total-click reads; detailed roll-ups come from an indexed events table."},
    ]
    data_model = {"links": ["code PK", "target_url", "created_at"] + (["expires_at"] if o["expiry"] else [])
                  + ["is_custom", "click_count", "last_click_at"]}
    if o["analytics"] == "detailed":
        data_model["click_events"] = ["id PK", "code FK->links ON DELETE CASCADE", "occurred_at", "day",
                                      "referrer_host", "user_agent"]
    scaling = [
        "Stateless API instances behind a load balancer; scale horizontally.",
        "Move storage to PostgreSQL (primary + read replicas); shard links by hash(code) beyond ~1B rows.",
        "Shared Redis cache in front of the DB for redirects (>95% hit rate expected on popular links).",
        "Replace the in-process click queue with Kafka; aggregate with a stream job into an OLAP store.",
        "CDN/edge redirects for the hottest codes; keep 302 semantics for analytics accuracy.",
    ]
    return {"style": "layered service (API -> service -> repository), async analytics pipeline",
            "components": components, "decisions": decisions, "data_model": data_model,
            "scaling_path": scaling, "options": o}


# ------------------------------------------------------------------- docs
def docs(options: dict[str, Any], contract: dict[str, Any], arch: dict[str, Any]) -> dict[str, str]:
    o = normalize_options(options)
    ops = []
    for path, methods in contract["paths"].items():
        for method, op in methods.items():
            codes = ", ".join(op["responses"])
            ops.append(f"| `{method.upper()}` | `{path}` | {op['summary']} | {codes} |")
    example_body = {"url": "https://example.com/very/long/path"}
    if o["custom_alias"]:
        example_body["custom_alias"] = "launch"
    if o["expiry"]:
        example_body["expires_in_seconds"] = 86400
    features = ["Short link creation with random base62 codes"]
    if o["custom_alias"]:
        features.append("Custom aliases (3-32 chars, reserved words blocked)")
    if o["expiry"]:
        features.append("Optional link expiry (expired links return 410 Gone)")
    features.append("302 redirects with async click recording")
    features.append("Analytics: total clicks + last click" +
                    (", clicks per day, top referrers" if o["analytics"] == "detailed" else ""))
    if o["rate_limit"]:
        features.append("Per-client rate limiting on link creation (429 + Retry-After)")
    readme = "\n".join([
        "# URL Shortener Service", "",
        "A small, production-structured URL shortener with a REST API, SQLite persistence and click analytics. "
        "Standard library only (Python 3.10+).", "",
        "## Features", *[f"- {f}" for f in features], "",
        "## Run", "```bash", "python -m shortener.server --port 8080", "```", "",
        "## Use", "```bash",
        f"curl -s -X POST localhost:8080/api/v1/links -H 'Content-Type: application/json' -d '{json.dumps(example_body)}'",
        "curl -i localhost:8080/<code>            # 302 redirect",
        "curl -s localhost:8080/api/v1/links/<code>/stats", "```", "",
        "## API", "Full contract: [`api/openapi.json`](../api/openapi.json)", "",
        "| Method | Path | Purpose | Responses |", "|---|---|---|---|", *ops, "",
        "Errors always have the shape `{\"error\": {\"code\": \"...\", \"message\": \"...\"}}`.", "",
        "## Configuration", "Environment variables `SHORTENER_<FIELD>` override `shortener/config.py` defaults, e.g. "
        "`SHORTENER_DATABASE_PATH`, `SHORTENER_BASE_URL`, `SHORTENER_CODE_LENGTH`"
        + (", `SHORTENER_RATE_LIMIT_PER_MINUTE`" if o["rate_limit"] else "") + ".", "",
        "## Test", "```bash", "pip install pytest && python -m pytest", "```",
        "Unit tests cover codec, validation, cache, service rules and analytics; integration tests drive the WSGI app, "
        "a real threaded HTTP server (concurrency) and check the implementation against the OpenAPI contract.", "",
    ])
    arch_md = "\n".join([
        "# Architecture", "", f"**Style:** {arch['style']}", "",
        "```mermaid", "flowchart LR",
        "    C[Client] -->|POST /api/v1/links| A[API layer]",
        "    C -->|GET /code| A",
        "    A --> S[ShortenerService]",
        "    S --> K[(Redirect cache)]",
        "    S --> R[(Repository / SQLite)]",
        "    S -->|enqueue click| Q[Click recorder queue]",
        "    Q -->|batched writes| R", "```", "",
        "## Components", "| Component | Module | Responsibility |", "|---|---|---|",
        *[f"| {c['name']} | `{c['module']}` | {c['responsibility']} |" for c in arch["components"]], "",
        "## Key decisions",
        *[f"### {d['decision']}\n- **Alternatives:** {d['alternatives']}\n- **Why:** {d['rationale']}\n"
          for d in arch["decisions"]],
        "## Data model", *[f"- **{t}**: {', '.join(cols)}" for t, cols in arch["data_model"].items()],
        "", "Schema: `shortener/storage/schema.sql`.", "",
        "## Request flow (redirect)",
        "1. API matches `/{code}` and calls `ShortenerService.resolve`.",
        "2. Cache hit returns the link; on miss the repository is read and the cache filled.",
        ("3. Expired links raise `Gone` (410).\n" if o["expiry"] else "")
        + "4. A `ClickEvent` is enqueued (no I/O) and the API returns `302` with `Location`.",
        "5. The background recorder flushes events in batches and updates counters.", "",
        "## Scaling path", *[f"- {s}" for s in arch["scaling_path"]], "",
        f"Design target: {o['scale_target']}.", "",
    ])
    return {"docs/README.md": readme, "docs/ARCHITECTURE.md": arch_md}


# ------------------------------------------------------- fault injection
def corrupt(files: dict[str, str]) -> dict[str, str]:
    """Introduce a realistic, test-detectable defect (used to demo the repair loop)."""
    out = dict(files)
    if "shortener/api.py" in out:
        out["shortener/api.py"] = out["shortener/api.py"].replace(
            'return Response(302, None, {"Location": target',
            'return Response(200, None, {"Location": target')
    return out
