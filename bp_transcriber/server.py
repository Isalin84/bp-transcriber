"""Локальный HTTP-сервер UI и медиа (Bottle на многопоточном wsgiref, только 127.0.0.1).

Почему свой сервер, а не WSGI-приложение в ``webview.create_window(url=app)``:
pywebview 6 в этом случае сам выбирает порт (``_get_random_port``: bind → close →
повторный bind, есть гонка) и не даёт публичного способа узнать адрес до старта GUI.
Свой сервер привязывается к сокету сразу, порт известен до создания окна (нужен для
``media_url``), а тесты гоняют ровно тот же код.

Маршруты:
* ``/`` → ``ui/index.html`` (или ``ui/_dev_probe.html``, пока UI нет);
* ``/media/<id>?k=<ключ>`` → ``preview.m4a`` записи истории, с поддержкой Range (206);
  ключ случайный на запуск, без него — 403;
* ``/<path>`` → статика из ``ui/``.
Защита от DNS-rebinding: принимаются только Host ``127.0.0.1:<port>`` / ``localhost:<port>``.
"""

from __future__ import annotations

import logging
import secrets
import sys
import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import bottle

from .history import HistoryStore, is_valid_id

logger = logging.getLogger(__name__)

PREFERRED_PORT = 47815  # стабильный origin → сохраняется localStorage WebView

MIME_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".txt": "text/plain",
}


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    # На Windows SO_REUSEADDR позволяет «перехватить» занятый порт — отключаем.
    allow_reuse_address = sys.platform != "win32"


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("http %s - %s", self.address_string(), format % args)

    def address_string(self) -> str:  # без обратного DNS
        return self.client_address[0]


def _mimetype(path: str) -> str:
    return MIME_TYPES.get(Path(path).suffix.lower(), "auto")


class AppServer:
    def __init__(self, ui_root: Path, history: HistoryStore, *, preferred_port: int | None = PREFERRED_PORT):
        self.ui_root = Path(ui_root)
        self.history = history
        self.preferred_port = preferred_port
        self.key = secrets.token_urlsafe(18)
        self.port: int | None = None
        self._httpd: _ThreadingWSGIServer | None = None
        self._thread: threading.Thread | None = None
        self.app = self._build_app()

    # --- жизненный цикл --------------------------------------------------------------

    def start(self) -> AppServer:
        httpd = None
        for port in ([self.preferred_port] if self.preferred_port else []) + [0]:
            try:
                httpd = make_server(
                    "127.0.0.1", port, self.app, server_class=_ThreadingWSGIServer, handler_class=_QuietHandler
                )
                break
            except OSError as exc:
                logger.info("Порт %s занят (%s), выбираю свободный", port, exc)
        if httpd is None:
            raise RuntimeError("Не удалось запустить локальный сервер")
        self._httpd = httpd
        self.port = httpd.server_port
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.25},
                                        name="bp-http", daemon=True)
        self._thread.start()
        logger.info("Локальный сервер: %s", self.base_url)
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("Сервер не запущен")
        return f"http://127.0.0.1:{self.port}/"

    def media_url(self, transcript_id: str) -> str | None:
        if self.port is None or self.history.preview_file(transcript_id) is None:
            return None
        return f"{self.base_url}media/{transcript_id}?k={self.key}"

    # --- приложение ------------------------------------------------------------------

    def _index_file(self) -> str:
        return "index.html" if (self.ui_root / "index.html").is_file() else "_dev_probe.html"

    def _build_app(self) -> bottle.Bottle:
        app = bottle.Bottle()
        app.catchall = True

        @app.hook("before_request")
        def check_host() -> None:
            host = (bottle.request.get_header("Host") or "").lower()
            allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
            if host not in allowed:
                raise bottle.HTTPError(403, "Forbidden")

        def no_cache(resp: Any) -> Any:
            resp.set_header("Cache-Control", "no-cache")
            return resp

        @app.get("/")
        def index() -> Any:
            name = self._index_file()
            return no_cache(bottle.static_file(name, root=str(self.ui_root), mimetype="text/html"))

        @app.get("/media/<tid>")
        def media(tid: str) -> Any:
            key = bottle.request.query.get("k") or ""
            if not secrets.compare_digest(key.encode(), self.key.encode()):
                raise bottle.HTTPError(403, "Forbidden")
            if not is_valid_id(tid):
                raise bottle.HTTPError(404, "Not found")
            preview = self.history.preview_file(tid)
            if preview is None:
                raise bottle.HTTPError(404, "Not found")
            resp = bottle.static_file(preview.name, root=str(preview.parent), mimetype="audio/mp4")
            resp.set_header("Cache-Control", "private, max-age=3600")
            return resp

        @app.get("/<path:path>")
        def static(path: str) -> Any:
            return no_cache(bottle.static_file(path, root=str(self.ui_root), mimetype=_mimetype(path)))

        return app

