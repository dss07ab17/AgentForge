"""Shared fixtures: in-memory repository, deterministic clocks and a WSGI test client."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from wsgiref.util import setup_testing_defaults

import pytest

from shortener.analytics import ClickRecorder
from shortener.api import Application
from shortener.cache import TTLCache
from shortener.config import Settings
from shortener.service import ShortenerService
from shortener.storage import SQLiteRepository


class FakeClock:
    """Controllable UTC clock so time-dependent behaviour is tested deterministically."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class WSGIClient:
    def __init__(self, app) -> None:
        self.app = app

    def request(self, method: str, path: str, json_body=None, headers: dict | None = None, raw: bytes | None = None):
        body = raw if raw is not None else (json.dumps(json_body).encode() if json_body is not None else b"")
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)),
                   "CONTENT_TYPE": "application/json", "wsgi.input": io.BytesIO(body)}
        for key, value in (headers or {}).items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value
        setup_testing_defaults(environ)
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(response_headers)

        payload = b"".join(self.app(environ, start_response))
        data = json.loads(payload) if payload and captured["headers"].get("Content-Type") == "application/json" else None
        return captured["status"], captured["headers"], data


@pytest.fixture
def settings() -> Settings:
    return Settings(database_path=":memory:", base_url="https://sho.rt", rate_limit_per_minute=1000)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def repo(settings):
    repository = SQLiteRepository(settings.database_path)
    yield repository
    repository.close()


@pytest.fixture
def recorder(repo) -> ClickRecorder:
    return ClickRecorder(repo)  # not started: tests call flush() explicitly


@pytest.fixture
def service(repo, settings, recorder, clock) -> ShortenerService:
    return ShortenerService(repo, settings, cache=TTLCache(100, 60), recorder=recorder, clock=clock)


@pytest.fixture
def client(service, settings) -> WSGIClient:
    return WSGIClient(Application(service, settings))
