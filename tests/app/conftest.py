"""Фикстуры тестов приложения: изолированные каталоги, тестовый результат, фейковая шина."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment, WordSegment


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Все пользовательские каталоги и токены — во временной папке, без реального keyring."""
    monkeypatch.setenv("BP_HOME", str(tmp_path / "bphome"))
    for var in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HF_TOKEN_PATH", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hfhome"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "userhome"))
    # .env ищем только в текущем каталоге теста, а не в корне репозитория разработчика
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bp_transcriber.settings._dotenv_dirs", lambda: [Path.cwd()])
    yield


def make_result(source: str = "/tmp/встреча.mp3") -> TranscriptionResult:
    def words(text: str, start: float, end: float) -> list[WordSegment]:
        parts = text.split()
        step = (end - start) / len(parts)
        return [WordSegment(w, round(start + i * step, 3), round(start + (i + 1) * step, 3)) for i, w in enumerate(parts)]

    segs = [
        TranscriptionSegment("Добрый день, коллеги.", 0.5, 2.0, speaker="Спикер 1"),
        TranscriptionSegment("Начнём с итогов квартала.", 2.2, 4.1, speaker="Спикер 1"),
        TranscriptionSegment("Продажи выросли на ёлочные игрушки.", 4.5, 7.25, speaker="Спикер 2"),
        TranscriptionSegment("Отлично, спасибо.", 3661.001, 3662.5, speaker="Спикер 1"),
    ]
    for s in segs:
        s.words = words(s.text, s.start, s.end)
    return TranscriptionResult(
        text=" ".join(s.text for s in segs),
        segments=segs,
        duration=3663.0,
        language="ru",
        model_name="v3_e2e_rnnt",
        processing_time=12.5,
        metadata={"source": source, "device": "CPU", "diarization": "hybrid", "num_speakers": 2,
                  "pipeline_version": 2, "timings": {}},
    )


@pytest.fixture
def result() -> TranscriptionResult:
    return make_result()


class RecordingBus:
    """Шина-заглушка: просто запоминает события (потокобезопасно)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def post(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def of_type(self, etype: str) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["type"] == etype]

    def wait_for(self, predicate, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for e in self.events:
                    if predicate(e):
                        return e
            time.sleep(0.01)
        raise AssertionError("событие не дождались")


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()
