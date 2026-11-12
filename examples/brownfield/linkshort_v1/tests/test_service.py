import pytest

from app import service
from app.db import connect


@pytest.fixture
def conn():
    return connect(":memory:")


def test_shorten_and_resolve(conn):
    code = service.shorten(conn, "https://example.com")
    assert service.resolve(conn, code) == "https://example.com"


def test_rejects_non_http_urls(conn):
    with pytest.raises(ValueError):
        service.shorten(conn, "ftp://example.com")


def test_unknown_code(conn):
    with pytest.raises(service.LinkNotFound):
        service.resolve(conn, "zzzzz")
