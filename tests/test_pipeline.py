"""
Тесты TranscriptionPipeline на поддельных этапах (без моделей и ffmpeg).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from gigaam_transcriber import pipeline as pl
from gigaam_transcriber.asr_engine import AsrSegment
from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
from gigaam_transcriber.data_models import WordSegment
from gigaam_transcriber.diarization import PyannoteUnavailableError, SpeakerTurn
from gigaam_transcriber.exceptions import DiarizationError, EmptyAudioError
from gigaam_transcriber.pipeline import PipelineOptions, TranscriptionPipeline
from gigaam_transcriber.progress import Cancelled, CancelToken
from gigaam_transcriber.vad import Chunk

WORDS = [
    WordSegment("Добрый", 0.5, 0.9),
    WordSegment("день.", 1.0, 1.4),
    WordSegment("Здравствуйте!", 2.5, 3.2),
]


class FakeEngine:
    def __init__(self, segments=None) -> None:
        self.loaded = False
        self.loads = 0
        self.unloads = 0
        self.segments = [AsrSegment(0.4, 3.3, "Добрый день. Здравствуйте!", list(WORDS))] if segments is None else segments
        self.device_label = "CPU"

    def load(self, *, on_progress=None, cancel=None):
        self.loads += 1
        on_progress and on_progress(0.5)
        self.loaded = True

    def unload(self):
        self.unloads += 1
        self.loaded = False

    def transcribe(self, audio, chunks, *, on_progress=None, cancel=None):
        for fraction in (0.25, 0.5, 1.0):
            if cancel is not None:
                cancel.raise_if_cancelled()
            on_progress and on_progress(fraction)
        return self.segments


class FakeDiarizer:
    calls: list[str] = []
    error: Exception | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.model_id = "fake/pyannote"
        self.model_kind = "fake"

    def diarize(self, audio, *args, on_progress=None, cancel=None, **kwargs):
        FakeDiarizer.calls.append(type(self).__name__)
        if type(self).error is not None:
            raise type(self).error
        on_progress and on_progress(0.5)
        return [SpeakerTurn(0.0, 2.0, "SPEAKER_00"), SpeakerTurn(2.0, 4.0, "SPEAKER_01")]


class FakePyannote(FakeDiarizer):
    error = None


class FakeHybrid(FakeDiarizer):
    error = None


@pytest.fixture
def setup(monkeypatch, tmp_path):
    audio = DecodedAudio(pcm=np.zeros(4 * SAMPLE_RATE, dtype=np.int16), source=tmp_path / "a.wav")

    def fake_decode(path, *, on_progress=None, cancel=None):
        on_progress and on_progress(1.0)
        return audio

    monkeypatch.setattr(pl.audio_io, "decode", fake_decode)
    monkeypatch.setattr(pl.vad, "detect_speech", lambda a, on_progress=None, cancel=None: [(0.4, 3.3)])
    monkeypatch.setattr(pl, "PyannoteDiarizer", FakePyannote)
    monkeypatch.setattr(pl, "HybridDiarizer", FakeHybrid)
    monkeypatch.setattr(pl, "pyannote_cached", lambda: False)
    FakeDiarizer.calls = []
    FakePyannote.error = None
    FakeHybrid.error = None
    pipe = TranscriptionPipeline(device="cpu")
    engine = FakeEngine()
    pipe._engine = engine
    return pipe, engine, tmp_path / "a.wav"


class TestRun:
    def test_none_mode(self, setup):
        pipe, engine, path = setup
        events = []
        result = pipe.run(path, PipelineOptions(diarization="none"), on_event=events.append)

        assert result.text == "Добрый день. Здравствуйте!"
        # пауза 1.1 с > max_gap 1.0 -> два сегмента, слова сохранены
        assert [s.text for s in result.segments] == ["Добрый день.", "Здравствуйте!"]
        assert all(s.speaker is None for s in result.segments)
        assert [w for s in result.segments for w in s.words] == WORDS
        assert result.duration == 4.0 and result.language == "ru"
        meta = result.metadata
        assert meta["diarization"] == "none" and meta["num_speakers"] == 0
        assert meta["pipeline_version"] == 2 and meta["device"] == "CPU"
        assert set(meta["timings"]) >= {"decode", "vad", "asr", "finalize"}
        assert "diarize" not in meta["timings"]
        # load отдельно (progress 0), остальные этапы — монотонно до 1
        assert events[0].stage == "load" and all(e.progress == 0.0 for e in events if e.stage == "load")
        work = [e for e in events if e.stage != "load"]
        assert all(b.progress >= a.progress for a, b in zip(work, work[1:]))
        assert work[-1].progress == pytest.approx(1.0)
        assert {e.stage for e in work} == {"decode", "vad", "asr", "finalize"}
        asr_end = max(e.progress for e in work if e.stage == "asr")
        assert asr_end == pytest.approx(0.70 / 0.70)  # без диаризации asr — до конца
        assert engine.loads == 1

    def test_messages_in_russian(self, setup):
        pipe, _, path = setup
        events = []
        pipe.run(path, PipelineOptions(diarization="hybrid"), on_event=events.append)
        messages = {e.stage: e.message for e in events}
        assert messages["decode"] == "Декодирование аудио"
        assert messages["vad"] == "Поиск речи"
        assert messages["asr"] == "Распознавание речи"
        assert messages["diarize"] == "Разделение по спикерам"
        assert messages["finalize"] == "Сохранение"

    def test_weights_with_diarization(self, setup):
        pipe, _, path = setup
        events = []
        pipe.run(path, PipelineOptions(diarization="hybrid"), on_event=events.append)
        asr_end = max(e.progress for e in events if e.stage == "asr")
        assert asr_end == pytest.approx(0.70)
        diarize = [e for e in events if e.stage == "diarize"]
        assert diarize[0].progress == pytest.approx(0.70)
        assert diarize[-1].progress == pytest.approx(1.0)

    def test_hybrid_speakers(self, setup):
        pipe, _, path = setup
        result = pipe.run(path, PipelineOptions(diarization="hybrid"))
        assert [(s.text, s.speaker) for s in result.segments] == [
            ("Добрый день.", "Спикер 1"),
            ("Здравствуйте!", "Спикер 2"),
        ]
        assert result.metadata["diarization"] == "hybrid"
        assert result.metadata["num_speakers"] == 2
        assert FakeDiarizer.calls == ["FakeHybrid"]

    def test_auto_without_token_uses_hybrid(self, setup):
        pipe, _, path = setup
        assert pipe.run(path, PipelineOptions()).metadata["diarization"] == "hybrid"

    def test_auto_with_token_uses_pyannote(self, setup):
        pipe, _, path = setup
        pipe.set_token("hf_x")
        result = pipe.run(path, PipelineOptions())
        assert result.metadata["diarization"] == "pyannote"
        assert result.metadata["diarization_model"] == "fake/pyannote"

    def test_auto_with_cache_uses_pyannote(self, setup, monkeypatch):
        pipe, _, path = setup
        monkeypatch.setattr(pl, "pyannote_cached", lambda: True)
        assert pipe.run(path, PipelineOptions()).metadata["diarization"] == "pyannote"

    def test_pyannote_failure_falls_back_to_hybrid(self, setup):
        pipe, _, path = setup
        FakePyannote.error = PyannoteUnavailableError("нет условий", "terms")
        events = []
        result = pipe.run(path, PipelineOptions(diarization="pyannote"), on_event=events.append)
        assert result.metadata["diarization"] == "hybrid"
        assert FakeDiarizer.calls == ["FakePyannote", "FakeHybrid"]
        assert result.metadata["warnings"]
        assert any("pyannote недоступен" in e.message for e in events)

    def test_hybrid_failure_keeps_text(self, setup):
        pipe, _, path = setup
        FakeHybrid.error = DiarizationError("нет модели")
        result = pipe.run(path, PipelineOptions(diarization="hybrid"))
        assert result.metadata["diarization"] == "none"
        assert result.text == "Добрый день. Здравствуйте!"
        assert all(s.speaker is None for s in result.segments)

    def test_empty_audio(self, setup, monkeypatch):
        pipe, _, path = setup
        monkeypatch.setattr(pl.vad, "detect_speech", lambda a, on_progress=None, cancel=None: [])
        with pytest.raises(EmptyAudioError):
            pipe.run(path, PipelineOptions(diarization="none"))

    def test_no_text(self, setup):
        pipe, engine, path = setup
        engine.segments = []
        with pytest.raises(EmptyAudioError):
            pipe.run(path, PipelineOptions(diarization="none"))

    def test_cancel_propagates_and_reuse(self, setup):
        pipe, _, path = setup
        token = CancelToken()

        def on_event(event):
            if event.stage == "asr":
                token.cancel()

        with pytest.raises(Cancelled):
            pipe.run(path, PipelineOptions(diarization="none"), on_event=on_event, cancel=token)
        assert pipe.run(path, PipelineOptions(diarization="none")).text

    def test_cancel_in_diarization_not_swallowed(self, setup):
        pipe, _, path = setup
        FakePyannote.error = Cancelled("Отменено пользователем")
        with pytest.raises(Cancelled):
            pipe.run(path, PipelineOptions(diarization="pyannote"))
        assert FakeDiarizer.calls == ["FakePyannote"]

    def test_invalid_mode(self, setup):
        pipe, _, path = setup
        with pytest.raises(ValueError):
            pipe.run(path, PipelineOptions(diarization="magic"))

    def test_preview(self, setup, monkeypatch):
        pipe, _, path = setup
        written = []
        monkeypatch.setattr(pl.audio_io, "encode_preview", lambda audio, out, cancel=None: written.append(out))
        pipe.run(path, PipelineOptions(diarization="none", preview_path=Path("p.m4a")))
        assert written == [Path("p.m4a")]

    def test_preview_failure_is_warning(self, setup, monkeypatch):
        pipe, _, path = setup

        def fail(audio, out, cancel=None):
            raise OSError("disk full")

        monkeypatch.setattr(pl.audio_io, "encode_preview", fail)
        result = pipe.run(path, PipelineOptions(diarization="none", preview_path=Path("p.m4a")))
        assert result.metadata["warnings"]


class TestSettings:
    def test_set_device_reloads_engine(self, setup, monkeypatch):
        pipe, engine, path = setup
        created = []

        class NewEngine(FakeEngine):
            def __init__(self, model_name, device):
                super().__init__()
                created.append(device)

        monkeypatch.setattr(pl, "GigaAMEngine", NewEngine)
        pipe.prepare()
        pipe.set_device("gpu")
        assert engine.loaded  # применяется только при следующем запуске
        pipe.run(path, PipelineOptions(diarization="none"))
        assert engine.unloads == 1 and created == ["gpu"]
        assert pipe.engine.loaded

    def test_prepare_emits_load_events(self, setup):
        pipe, _, _ = setup
        events = []
        pipe.prepare(on_event=events.append)
        assert {e.stage for e in events} == {"load"}
        assert events[0].stage_progress == 0.0 and events[-1].stage_progress == 1.0
        assert events[0].message == "Загрузка модели"

    def test_close(self, setup):
        pipe, engine, _ = setup
        pipe.prepare()
        pipe.close()
        assert not engine.loaded

    def test_set_same_device_no_reload(self, setup):
        pipe, engine, path = setup
        pipe.set_device("cpu")
        pipe.run(path, PipelineOptions(diarization="none"))
        assert engine.unloads == 0


class TestProgress:
    def test_eta_from_stage_rate(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(pl.time, "monotonic", lambda: now[0])
        events = []
        progress = pl._Progress(events.append, ["decode", "vad", "asr", "finalize"])
        progress.start("asr")
        progress.progress = 0.1 / 0.7  # decode+vad завершены
        now[0] += 10.0
        progress.update(0.5, force=True)
        last = events[-1]
        # asr: 0.5 * (0.6/0.7) за 10 с; осталось 1 - (0.1/0.7 + 0.3/0.7) = 0.3/0.7
        rate = 0.5 * (0.6 / 0.7) / 10.0
        assert last.eta_s == pytest.approx((0.3 / 0.7) / rate)

    def test_throttling(self, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(pl.time, "monotonic", lambda: now[0])
        events = []
        progress = pl._Progress(events.append, ["asr"])
        progress.start("asr")
        for i in range(1, 100):
            progress.update(i / 1000)
        assert len(events) == 1  # только событие start
        progress.update(1.0)
        assert events[-1].stage_progress == 1.0
