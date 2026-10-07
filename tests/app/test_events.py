from __future__ import annotations

import json
import threading
import time

from bp_transcriber.events import EventBus, to_js


class Sink:
    def __init__(self):
        self.calls: list[tuple[float, dict]] = []
        self.lock = threading.Lock()

    def __call__(self, script: str) -> None:
        prefix = "window.bp && window.bp.onEvent("
        assert script.startswith(prefix) and script.endswith(")")
        event = json.loads(script[len(prefix):-1])
        with self.lock:
            self.calls.append((time.monotonic(), event))

    def events(self) -> list[dict]:
        with self.lock:
            return [e for _, e in self.calls]

    def wait(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.events()):
                return
            time.sleep(0.005)
        raise AssertionError(f"не дождались: {self.events()}")


def job_update(job_id: str, status: str = "running", progress: float = 0.0) -> dict:
    return {"type": "job_update", "job": {"id": job_id, "status": status, "progress": progress}}


def test_to_js_keeps_unicode():
    script = to_js({"type": "toast", "message": "Привет мир"})
    assert "Привет" in script and "\\u2028" in script


def test_terminal_delivered_immediately_and_drops_pending():
    sink = Sink()
    bus = EventBus(sink, max_rate_hz=1).start()  # 1 Гц: промежуточное «застрянет»
    bus.set_ready(True)
    try:
        bus.post(job_update("a", progress=0.1))
        sink.wait(lambda ev: len(ev) == 1)
        bus.post(job_update("a", progress=0.5))  # ждёт окна в 1 с
        t0 = time.monotonic()
        bus.post(job_update("a", status="cancelled"))
        sink.wait(lambda ev: len(ev) == 2)
        assert time.monotonic() - t0 < 0.5
        time.sleep(0.2)
    finally:
        bus.stop()
    events = sink.events()
    assert [e["job"].get("status") for e in events] == ["running", "cancelled"]


def test_order_kept_across_types_and_jobs():
    sink = Sink()
    bus = EventBus(sink).start()
    posted = [
        job_update("a", status="queued"),
        job_update("b", status="queued"),
        {"type": "toast", "level": "info", "message": "1"},
        job_update("a", status="error"),
        {"type": "files_dropped", "paths": ["/x"]},
        job_update("b", status="done"),
    ]
    for e in posted:
        bus.post(e)
    bus.set_ready(True)  # до ready всё буферизуется
    try:
        # промежуточные состояния, вытесненные терминальными, не доставляются
        expected = [posted[2], posted[3], posted[4], posted[5]]
        sink.wait(lambda ev: len(ev) == len(expected))
        time.sleep(0.1)
    finally:
        bus.stop()
    assert sink.events() == expected


def test_buffer_until_ready_and_stop():
    sink = Sink()
    bus = EventBus(sink).start()
    bus.post({"type": "toast", "level": "info", "message": "early"})
    time.sleep(0.1)
    assert sink.events() == []
    bus.set_ready(True)
    sink.wait(lambda ev: len(ev) == 1)
    bus.stop()
    bus.post({"type": "toast", "level": "info", "message": "late"})
    time.sleep(0.05)
    assert len(sink.events()) == 1


def test_sink_errors_do_not_kill_dispatcher():
    calls = []

    def flaky(script):
        calls.append(script)
        if len(calls) == 1:
            raise RuntimeError("window closed")

    bus = EventBus(flaky).start()
    bus.set_ready(True)
    bus.post({"type": "toast", "level": "info", "message": "1"})
    bus.post({"type": "toast", "level": "info", "message": "2"})
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    bus.stop()
    assert len(calls) == 2


def test_model_download_coalesced():
    sink = Sink()
    bus = EventBus(sink, max_rate_hz=5).start()
    bus.set_ready(True)
    try:
        for i in range(100):
            bus.post({"type": "model_download", "status": "downloading", "progress": i / 100})
        bus.post({"type": "model_download", "status": "done", "progress": 1.0})
        sink.wait(lambda ev: ev and ev[-1]["status"] == "done")
    finally:
        bus.stop()
    assert len(sink.events()) <= 3
