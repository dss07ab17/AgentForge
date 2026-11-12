"""Business logic for shortening and resolving links."""
from app import store
from app.codes import new_code


class LinkNotFound(Exception):
    pass


def shorten(conn, url, code_factory=new_code):
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    code = code_factory()
    store.save_link(conn, code, url)
    return code


def resolve(conn, code):
    url = store.get_url(conn, code)
    if url is None:
        raise LinkNotFound(code)
    return url
