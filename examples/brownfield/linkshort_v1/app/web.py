"""WSGI front end: POST /shorten and GET /<code>."""
import json

from app import service
from app.db import connect

CONN = None


def get_conn():
    global CONN
    if CONN is None:
        CONN = connect()
    return CONN


def _json(start_response, status, payload):
    body = json.dumps(payload).encode()
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def app(environ, start_response):
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")
    conn = get_conn()
    if method == "POST" and path == "/shorten":
        length = int(environ.get("CONTENT_LENGTH") or 0)
        data = json.loads(environ["wsgi.input"].read(length) or b"{}")
        try:
            code = service.shorten(conn, data.get("url", ""))
        except ValueError as exc:
            return _json(start_response, "400 Bad Request", {"error": str(exc)})
        return _json(start_response, "201 Created", {"code": code})
    if method == "GET" and len(path) > 1:
        try:
            url = service.resolve(conn, path[1:])
        except service.LinkNotFound:
            return _json(start_response, "404 Not Found", {"error": "not found"})
        start_response("302 Found", [("Location", url), ("Content-Length", "0")])
        return [b""]
    return _json(start_response, "404 Not Found", {"error": "not found"})
