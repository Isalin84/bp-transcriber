"""Шина событий Python → JS (``window.bp.onEvent``).

Модель потоков: ``post()`` вызывается из любых потоков и только кладёт событие в
очередь под ``_cond`` (никогда не блокируется на GUI). Единственный поток-диспетчер
доставляет события через ``sink`` (по умолчанию ``window.evaluate_js``) ВНЕ блокировки,
поэтому медленный/зависший WebView не блокирует воркеры.

Коалесцирование: нетерминальные ``job_update`` (ключ — id задачи) и прогресс
``model_download`` хранятся по одному последнему на ключ и доставляются не чаще
``max_rate_hz``. Терминальные события (status done/error/cancelled, ``job_done`` и
все прочие типы) идут в FIFO сразу; терминальное событие отбрасывает ожидающее
промежуточное по тому же ключу, т.к. содержит более свежее полное состояние.
Порядок доставки — по порядковому номеру публикации.

До ``set_ready(True)`` (событие ``loaded`` окна) всё буферизуется.

Важно: cocoa ``evaluate_js`` ждёт главный поток без таймаута, поэтому ``stop()`` не
ждёт диспетчер бесконечно — поток daemon.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = frozenset({"done", "error", "cancelled"})
MAX_BUFFERED = 5000


def coalesce_key(event: dict[str, Any]) -> tuple[str, str] | None:
    """Ключ для коалесцирования или None (событие терминальное/неколлапсируемое)."""
    etype = event.get("type")
    if etype == "job_update":
        job = event.get("job") or {}
        if job.get("status") not in TERMINAL_JOB_STATUSES:
            return ("job", str(job.get("id")))
    elif etype == "model_download" and event.get("status") == "downloading":
        return ("model_download", "")
    return None


def to_js(event: dict[str, Any]) -> str:
    payload = json.dumps(event, ensure_ascii=False, default=str)
    # U+2028/2029 допустимы в JSON, но ломают старые JS-парсеры.
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"window.bp && window.bp.onEvent({payload})"


class EventBus:
    def __init__(self, sink: Callable[[str], Any] | None = None, *, max_rate_hz: float = 10.0):
        self._sink = sink
        self._interval = 1.0 / max_rate_hz if max_rate_hz > 0 else 0.0
        self._cond = threading.Condition()
        self._fifo: deque[tuple[int, dict[str, Any]]] = deque()
        self._pending: dict[tuple[str, str], list[Any]] = {}  # key -> [seq, event]
        self._last_sent: dict[tuple[str, str], float] = {}
        self._seq = 0
        self._wait: float | None = None
        self._ready = False
        self._stopped = False
        self._thread: threading.Thread | None = None

    # --- управление -------------------------------------------------------------

    def attach_window(self, window: Any) -> None:
        self.set_sink(window.evaluate_js)

    def set_sink(self, sink: Callable[[str], Any] | None) -> None:
        with self._cond:
            self._sink = sink
            self._cond.notify_all()

    def set_ready(self, ready: bool) -> None:
        with self._cond:
            self._ready = ready
            self._cond.notify_all()

    def start(self) -> EventBus:
        with self._cond:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="bp-events", daemon=True)
                self._thread.start()
        return self

    def stop(self, timeout: float = 1.0) -> None:
        """Останавливает диспетчер; недоставленные события отбрасываются."""
        with self._cond:
            self._stopped = True
            self._ready = False
            self._sink = None
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    # --- публикация -------------------------------------------------------------

    def post(self, event: dict[str, Any]) -> None:
        key = coalesce_key(event)
        with self._cond:
            if self._stopped:
                return
            self._seq += 1
            if key is not None:
                slot = self._pending.get(key)
                if slot is None:
                    self._pending[key] = [self._seq, event]
                else:
                    slot[1] = event  # позиция в очереди сохраняется, содержимое — свежее
            else:
                term_key = self._terminal_key(event)
                if term_key is not None:
                    self._pending.pop(term_key, None)
                self._fifo.append((self._seq, event))
                if len(self._fifo) > MAX_BUFFERED:
                    dropped = self._fifo.popleft()
                    logger.warning("Буфер событий переполнен, отброшено: %s", dropped[1].get("type"))
            self._cond.notify_all()

    @staticmethod
    def _terminal_key(event: dict[str, Any]) -> tuple[str, str] | None:
        etype = event.get("type")
        if etype in ("job_update", "job_done"):
            job = event.get("job") or {}
            return ("job", str(job.get("id")))
        if etype == "model_download":
            return ("model_download", "")
        return None

    # --- диспетчер --------------------------------------------------------------

    def _next(self) -> tuple[dict[str, Any], tuple[str, str] | None] | None:
        """Под блокировкой: выбрать следующее событие или None (+ таймаут ожидания в self._wait)."""
        now = time.monotonic()
        best_seq: int | None = None
        best_key: tuple[str, str] | None = None
        wait: float | None = None
        for key, (seq, _event) in self._pending.items():
            due = self._last_sent.get(key, float("-inf")) + self._interval
            if due <= now:
                if best_seq is None or seq < best_seq:
                    best_seq, best_key = seq, key
            else:
                wait = due - now if wait is None else min(wait, due - now)
        if self._fifo and (best_seq is None or self._fifo[0][0] < best_seq):
            return self._fifo.popleft()[1], None
        if best_key is not None:
            _, event = self._pending.pop(best_key)
            return event, best_key
        self._wait = wait
        return None

    def _run(self) -> None:
        while True:
            with self._cond:
                while True:
                    if self._stopped:
                        return
                    if self._ready and self._sink is not None:
                        picked = self._next()
                        if picked is not None:
                            break
                        timeout = self._wait
                    else:
                        timeout = None
                    self._cond.wait(timeout)
                event, key = picked
                sink = self._sink
                if key is not None:
                    self._last_sent[key] = time.monotonic()
            try:
                sink(to_js(event))
            except Exception as exc:  # noqa: BLE001 — окно могло закрыться/перезагрузиться
                logger.debug("Не удалось доставить событие %s: %s", event.get("type"), exc)
