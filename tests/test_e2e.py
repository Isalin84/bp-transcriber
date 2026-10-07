"""
Сквозной сценарий на настоящих моделях: речь → очередь → GigaAM + hybrid → история → экспорт.

Речь синтезирует macOS ``say`` (голос Milena). Нужны скачанная модель GigaAM и ffmpeg;
без них тест пропускается.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time

import pytest

from bp_transcriber import exporters
from bp_transcriber.history import HistoryStore
from bp_transcriber.jobs import JobQueue
from bp_transcriber.settings import SettingsStore, TokenStore
from gigaam_transcriber.model_store import DEFAULT_MODEL, is_model_available

PHRASE = "Добрый день, коллеги. Сегодня мы обсуждаем план продаж на следующий квартал."

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("say"), reason="нужен macOS say"),
    pytest.mark.skipif(not is_model_available(DEFAULT_MODEL), reason="модель GigaAM не скачана"),
]


class Bus:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def post(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def wait_for(self, etype: str, timeout: float = 120.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for e in self.events:
                    if e["type"] == etype or (e["type"] == "job_update" and e["job"]["status"] == "error"):
                        return e
            time.sleep(0.05)
        raise AssertionError(f"не дождались {etype}")


def test_speech_to_exported_transcript(tmp_path):
    audio = tmp_path / "совещание.wav"
    subprocess.run(
        ["say", "-v", "Milena", "-o", str(audio), "--data-format=LEI16@16000", PHRASE], check=True
    )
    settings = SettingsStore(tmp_path / "settings.json", TokenStore(tmp_path / "tok", use_keyring=False))
    history = HistoryStore(tmp_path / "history")
    bus = Bus()
    queue = JobQueue(settings, history, bus)
    try:
        queue.enqueue([str(audio)], {"diarization": "hybrid"})
        done = bus.wait_for("job_done")
    finally:
        queue.shutdown(timeout=10)

    assert done["type"] == "job_done", done
    transcript = history.load(done["transcript_id"])
    text = exporters.render_txt(transcript).lower()
    for word in ("коллеги", "план", "квартал"):
        assert word in text, text
    assert len(transcript["speakers"]) == 1
    assert transcript["segments"][0]["words"]
