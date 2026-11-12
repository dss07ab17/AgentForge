"""Development server entry point: ``python -m shortener.server --port 8080``.

For production run the WSGI app under a process manager, e.g.
``gunicorn 'shortener.api:create_app()' --workers 4 --threads 8``.
"""
from __future__ import annotations

import argparse
import logging
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .api import create_app
from .config import Settings


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        logging.getLogger("shortener.access").info("%s " + fmt, self.client_address[0], *args)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the URL shortener (development server)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(Settings.from_env())
    with make_server(args.host, args.port, app, server_class=ThreadingWSGIServer, handler_class=QuietHandler) as httpd:
        logging.info("listening on http://%s:%d", args.host, args.port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.service.recorder.stop()  # flush buffered analytics before exit


if __name__ == "__main__":
    main()
