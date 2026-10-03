"""Сборка приложения: хранилища, шина, очередь, сервер, API и окно pywebview.

Потоки: главный — GUI-цикл pywebview; ``bp-http`` — локальный сервер; ``bp-events`` —
доставка событий в JS; ``bp-jobs`` — воркер транскрипции; методы API и DOM-обработчики
pywebview выполняются в своих потоках. Обработчик ``closing`` выполняется на главном
потоке синхронно, поэтому в нём только неблокирующие действия (отмена, остановка шины);
ожидание воркера — после выхода из ``webview.start()``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from typing import Any, TextIO

from . import __version__, paths
from .api import Api
from .events import EventBus
from .history import HistoryStore
from .jobs import JobQueue, ModelDownloads
from .server import AppServer
from .settings import SettingsStore

logger = logging.getLogger(__name__)

WINDOW_TITLE = "BP Transcriber"
BACKGROUND = "#0B1D3A"
SELFTEST_UI_TIMEOUT = 25.0

_DRAGOVER_JS = (
    "if (!window.__bpDragover) { window.__bpDragover = true;"
    " document.addEventListener('dragover', function (e) { e.preventDefault(); }); }"
)


class _QuietApiErrors(logging.Filter):
    """pywebview пишет полный traceback на каждое исключение API. Ожидаемые ``ApiError``
    (валидация, «не найдено») — это не сбой; неожиданные ошибки уже залогированы в ``api``."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage().rstrip()
        except Exception:  # noqa: BLE001
            return True
        return not message.rsplit("\n", 1)[-1].startswith("bp_transcriber.api.ApiError:")


class _DropBridge:
    """Drag&drop файлов из ОС: DOM-событие ``drop`` → событие ``files_dropped`` с полными путями.

    Регистрируется на каждый ``loaded``: после перезагрузки страницы JS-обработчики исчезают.
    pywebview 6.2 сам сбрасывает кэш DOM-элементов при загрузке; ``off()`` старого элемента —
    страховка для версий, где кэш сохраняется (иначе обработчик сработал бы дважды).
    """

    def __init__(self, window: Any, bus: EventBus):
        self._window = window
        self._bus = bus
        self._lock = threading.Lock()
        self._element: Any = None

    def register(self) -> None:
        from webview.dom import DOMEventHandler

        with self._lock:
            if self._element is not None:
                try:
                    self._element.off("drop", self._on_drop)
                except Exception:  # noqa: BLE001 — элемент уже недействителен после перезагрузки
                    logger.debug("Снятие старого обработчика drop", exc_info=True)
                self._element = None
            try:
                self._window.run_js(_DRAGOVER_JS)
                document = self._window.dom.document
                document.events.drop += DOMEventHandler(self._on_drop, prevent_default=True, stop_propagation=True)
                self._element = document
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось подключить drag&drop")

    def _on_drop(self, event: dict[str, Any]) -> None:
        files = ((event or {}).get("dataTransfer") or {}).get("files") or []
        paths_ = [f["pywebviewFullPath"] for f in files if isinstance(f, dict) and f.get("pywebviewFullPath")]
        if paths_:
            self._bus.post({"type": "files_dropped", "paths": paths_})
        elif files:
            self._bus.post(
                {"type": "toast", "level": "warning", "message": "Не удалось получить путь к файлу — используйте кнопку «Выбрать файлы»"}
            )


def _selftest_ui(window: Any, outcome: dict[str, Any]) -> None:
    """Скрытое окно: дождаться loaded, вызвать get_state через JS и закрыть окно."""
    try:
        if not window.events.loaded.wait(SELFTEST_UI_TIMEOUT):
            outcome["error"] = "окно не загрузилось"
            return
        done = threading.Event()
        box: dict[str, Any] = {}

        def callback(value: Any) -> None:
            box["value"] = value
            done.set()

        window.evaluate_js("window.pywebview.api.get_state()", callback)
        if not done.wait(SELFTEST_UI_TIMEOUT):
            outcome["error"] = "get_state не ответил"
            return
        value = box.get("value")
        if isinstance(value, dict) and value.get("version") == __version__:
            outcome["ok"] = True
            outcome["state_keys"] = sorted(value)
            outcome["url"] = window.get_current_url()
        else:
            outcome["error"] = f"неожиданный ответ get_state: {str(value)[:300]}"
    except Exception as exc:  # noqa: BLE001
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            logger.exception("destroy")


def run_app(*, debug: bool = False, selftest_ui: bool = False, console: TextIO | None = None) -> int:
    import webview

    logging.getLogger("pywebview").addFilter(_QuietApiErrors())
    settings = SettingsStore()
    history = HistoryStore()
    bus = EventBus().start()
    downloads = ModelDownloads(bus)
    jobs = JobQueue(settings, history, bus, downloads=downloads)
    server = AppServer(paths.ui_dir(), history).start()
    api = Api(settings, history, jobs, downloads, server)

    window = webview.create_window(
        WINDOW_TITLE,
        url=server.base_url,
        js_api=api,
        width=1180,
        height=780,
        min_size=(920, 640),
        background_color=BACKGROUND,
        text_select=True,
        hidden=selftest_ui,
    )
    api._attach_window(window)
    bus.attach_window(window)
    drops = _DropBridge(window, bus)

    def on_before_load() -> None:
        bus.set_ready(False)

    def on_loaded() -> None:
        bus.set_ready(True)
        drops.register()

    def on_closing() -> None:
        jobs.cancel_all()
        bus.stop(timeout=0)  # не ждать: диспетчер может ждать главный поток в evaluate_js

    window.events.before_load += on_before_load
    window.events.loaded += on_loaded
    window.events.closing += on_closing

    threading.Thread(target=api._warm_up, name="bp-warmup", daemon=True).start()

    outcome: dict[str, Any] = {"ok": False}
    watchdog: threading.Timer | None = None
    if selftest_ui:
        # Страховка от зависания GUI: жёсткий выход, если окно так и не закрылось.
        watchdog = threading.Timer(SELFTEST_UI_TIMEOUT * 2 + 10, lambda: os._exit(3))
        watchdog.daemon = True
        watchdog.start()

    storage = paths.cache_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(
            func=_selftest_ui if selftest_ui else None,
            args=(window, outcome) if selftest_ui else None,
            gui="edgechromium" if sys.platform == "win32" else None,
            debug=debug,
            private_mode=False,
            storage_path=str(storage),
        )
    finally:
        jobs.shutdown(timeout=5.0)
        server.stop()
        bus.stop(timeout=0.5)
        if watchdog is not None:
            watchdog.cancel()

    if selftest_ui:
        text = json.dumps({"selftest_ui": outcome}, ensure_ascii=False)
        logger.info(text)
        if console is not None:
            try:
                print(text, file=console, flush=True)
            except (OSError, ValueError):
                pass
        return 0 if outcome.get("ok") else 1
    return 0
