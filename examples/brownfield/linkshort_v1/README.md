# linkshort v1

Minimal link shortener.

- `POST /shorten` with `{"url": "..."}` -> `201 {"code": "..."}`
- `GET /<code>` -> `302` redirect

Run tests: `python -m pytest`
