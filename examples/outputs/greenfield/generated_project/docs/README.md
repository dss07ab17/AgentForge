# URL Shortener Service

A small, production-structured URL shortener with a REST API, SQLite persistence and click analytics. Standard library only (Python 3.10+).

## Features
- Short link creation with random base62 codes
- Custom aliases (3-32 chars, reserved words blocked)
- Optional link expiry (expired links return 410 Gone)
- 302 redirects with async click recording
- Analytics: total clicks + last click, clicks per day, top referrers
- Per-client rate limiting on link creation (429 + Retry-After)

## Run
```bash
python -m shortener.server --port 8080
```

## Use
```bash
curl -s -X POST localhost:8080/api/v1/links -H 'Content-Type: application/json' -d '{"url": "https://example.com/very/long/path", "custom_alias": "launch", "expires_in_seconds": 86400}'
curl -i localhost:8080/<code>            # 302 redirect
curl -s localhost:8080/api/v1/links/<code>/stats
```

## API
Full contract: [`api/openapi.json`](../api/openapi.json)

| Method | Path | Purpose | Responses |
|---|---|---|---|
| `GET` | `/healthz` | Liveness/readiness probe | 200 |
| `POST` | `/api/v1/links` | Create a short link | 201, 400, 413, 503, 409, 429 |
| `GET` | `/api/v1/links/{code}` | Get link metadata | 200, 404 |
| `DELETE` | `/api/v1/links/{code}` | Delete a link | 204, 404 |
| `GET` | `/api/v1/links/{code}/stats` | Click analytics for a link | 200, 404 |
| `GET` | `/{code}` | Redirect to the target URL | 302, 404, 410 |

Errors always have the shape `{"error": {"code": "...", "message": "..."}}`.

## Configuration
Environment variables `SHORTENER_<FIELD>` override `shortener/config.py` defaults, e.g. `SHORTENER_DATABASE_PATH`, `SHORTENER_BASE_URL`, `SHORTENER_CODE_LENGTH`, `SHORTENER_RATE_LIMIT_PER_MINUTE`.

## Test
```bash
pip install pytest && python -m pytest
```
Unit tests cover codec, validation, cache, service rules and analytics; integration tests drive the WSGI app, a real threaded HTTP server (concurrency) and check the implementation against the OpenAPI contract.
