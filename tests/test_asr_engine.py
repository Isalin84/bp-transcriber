"""
Тесты GigaAMEngine на поддельной модели (без весов GigaAM).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from gigaam_transcriber import asr_engine
from gigaam_transcriber.asr_engine import GigaAMEngine, _collate, _padded_spans
from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
from gigaam_transcriber.exceptions import ModelLoadError
from gigaam_transcriber.progress import Cancelled, CancelToken
from gigaam_transcriber.vad import Chunk


class FakeModel(torch.nn.Module):
    """Имитация GigaAMASR: текст = длительность входа, два слова на чанк."""

    def __init__(self, nan: bool = False, empty_for: set[int] | None = None) -> None:
        super().__init__()
        self.weight = torch.nn.Parameter(torch.zeros(1))
        self.nan = nan
        self.empty_for = empty_for or set()
        self.batches: list[list[int]] = []
        self.on_forward = None

    def forward(self, wav, lengths):
        assert wav.dtype == self.weight.dtype
        self.batches.append(lengths.tolist())
        if self.on_forward:
            self.on_forward()
        encoded = torch.full((len(lengths), 4, 10), float("nan") if self.nan else 0.0)
        return encoded, lengths // 160

    def _decode(self, encoded, encoded_len, wav_lens, word_timestamps=False):
        out = []
        for n in wav_lens.tolist():
            duration = n / SAMPLE_RATE
            if n in self.empty_for:
                out.append(("", []))
                continue
            words = [
                SimpleNamespace(text="начало", start=0.1, end=0.3),
                SimpleNamespace(text="конец.", start=duration - 0.2, end=duration + 0.05),
            ]
            out.append((f"начало конец. {n}", words))
        return out


def make_audio(seconds: float) -> DecodedAudio:
    return DecodedAudio(pcm=np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16), source=Path("x.wav"))


def loaded_engine(model: FakeModel) -> GigaAMEngine:
    engine = GigaAMEngine(device="cpu")
    engine._model = model
    engine._device = torch.device("cpu")
    return engine


class TestPaddedSpans:
    def test_pad_bounded_by_neighbors_and_audio(self):
        chunks = [Chunk(10.0, 12.0), Chunk(0.1, 5.0), Chunk(5.2, 9.0)]
        spans = _padded_spans(chunks, duration=12.1)
        assert spans[1].start == 0.0  # не меньше 0
        assert spans[1].end == pytest.approx(5.1)  # середина паузы 5.0..5.2
        assert spans[2].start == pytest.approx(5.1)
        assert spans[2].end == pytest.approx(9.3)
        assert spans[0].start == pytest.approx(9.7)
        assert spans[0].end == pytest.approx(12.1)  # конец аудио

    def test_spans_never_overlap(self):
        rng = np.random.default_rng(0)
        bounds = np.cumsum(rng.uniform(0.05, 3.0, 40))
        chunks = [Chunk(float(a), float(b)) for a, b in zip(bounds[::2], bounds[1::2])]
        spans = sorted(_padded_spans(chunks, float(bounds[-1]) + 1), key=lambda c: c.start)
        for a, b in zip(spans, spans[1:]):
            assert a.end <= b.start + 1e-9
        for chunk, span in zip(chunks, _padded_spans(chunks, float(bounds[-1]) + 1)):
            assert span.start <= chunk.start and span.end >= chunk.end


class TestCollate:
    def test_padding(self):
        batch, lengths = _collate([torch.ones(3), torch.ones(5)])
        assert batch.shape == (2, 5)
        assert lengths.tolist() == [3, 5]
        assert batch[0].tolist() == [1, 1, 1, 0, 0]


class TestTranscribe:
    def test_order_offsets_and_batches(self, monkeypatch):
        monkeypatch.setenv("BP_ASR_BATCH", "2")
        model = FakeModel()
        engine = loaded_engine(model)
        chunks = [Chunk(20.0, 21.0), Chunk(0.0, 5.0), Chunk(10.0, 13.0), Chunk(30.0, 30.5), Chunk(40.0, 44.0)]
        audio = make_audio(45.0)
        progress: list[float] = []

        segments = engine.transcribe(audio, chunks, on_progress=progress.append)

        spans = _padded_spans(chunks, audio.duration)
        # хронологический порядок, ни один чанк не потерян
        assert [round(s.start, 3) for s in segments] == sorted(round(sp.start, 3) for sp in spans)
        assert len(segments) == len(chunks)
        # пакеты по 2, длины по убыванию
        flat = [n for batch in model.batches for n in batch]
        assert [len(b) for b in model.batches] == [2, 2, 1]
        assert flat == sorted(flat, reverse=True)
        # текст сегмента соответствует своему чанку, слова — в абсолютном времени
        by_start = {round(sp.start, 3): sp for sp in spans}
        for segment in segments:
            span = by_start[round(segment.start, 3)]
            samples = int(round(span.end * SAMPLE_RATE)) - int(round(span.start * SAMPLE_RATE))
            assert segment.text.endswith(str(samples))
            assert segment.words[0].start == pytest.approx(span.start + 0.1, abs=1e-3)
            assert segment.words[-1].end <= span.end + 1e-9  # конец слова не выходит за чанк
        assert progress[-1] == 1.0
        assert progress == sorted(progress)

    def test_empty_text_dropped(self):
        audio = make_audio(10.0)
        chunks = [Chunk(1.0, 2.0), Chunk(5.0, 7.0)]
        spans = _padded_spans(chunks, audio.duration)
        empty_len = int(round(spans[0].end * SAMPLE_RATE)) - int(round(spans[0].start * SAMPLE_RATE))
        engine = loaded_engine(FakeModel(empty_for={empty_len}))
        segments = engine.transcribe(audio, chunks)
        assert len(segments) == 1
        assert segments[0].start == pytest.approx(spans[1].start)

    def test_cancel_between_batches_and_reuse(self, monkeypatch):
        monkeypatch.setenv("BP_ASR_BATCH", "1")
        model = FakeModel()
        engine = loaded_engine(model)
        token = CancelToken()
        model.on_forward = token.cancel
        chunks = [Chunk(0.0, 1.0), Chunk(2.0, 3.0), Chunk(4.0, 5.0)]
        with pytest.raises(Cancelled):
            engine.transcribe(make_audio(6.0), chunks, cancel=token)
        assert len(model.batches) == 1
        model.on_forward = None
        assert len(engine.transcribe(make_audio(6.0), chunks)) == 3

    def test_cpu_thread_policy_applied_and_restored(self, monkeypatch):
        monkeypatch.setenv("BP_ASR_THREADS", "1")
        before = torch.get_num_threads()
        model = FakeModel()
        seen: list[int] = []
        model.on_forward = lambda: seen.append(torch.get_num_threads())
        loaded_engine(model).transcribe(make_audio(3.0), [Chunk(0.0, 2.0)])
        assert seen == [1]
        assert torch.get_num_threads() == before

    def test_no_chunks(self):
        assert loaded_engine(FakeModel()).transcribe(make_audio(1.0), []) == []

    def test_model_failure_wrapped(self):
        model = FakeModel()

        def boom():
            raise RuntimeError("kernel")

        model.on_forward = boom
        from gigaam_transcriber.exceptions import TranscriberError

        with pytest.raises(TranscriberError):
            loaded_engine(model).transcribe(make_audio(3.0), [Chunk(0.0, 2.0)])


class TestBatchSize:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("BP_ASR_BATCH", raising=False)
        assert asr_engine._batch_size("cpu") == 4
        assert asr_engine._batch_size("mps") == 8
        assert asr_engine._batch_size("cuda") == 16

    @pytest.mark.parametrize("raw, expected", [("3", 3), ("0", 4), ("abc", 4), ("-1", 4)])
    def test_env(self, monkeypatch, raw, expected):
        monkeypatch.setenv("BP_ASR_BATCH", raw)
        assert asr_engine._batch_size("cpu") == expected


class TestLoad:
    def test_gpu_nan_falls_back_to_cpu(self, monkeypatch, tmp_path):
        monkeypatch.setattr(asr_engine, "ensure_model", lambda name, on_progress=None, cancel=None: tmp_path)
        engine = GigaAMEngine(device="gpu")
        monkeypatch.setattr(engine, "_target_device", lambda: torch.device("mps"))
        loads: list[str] = []

        def fake_load(device, root):
            loads.append(device.type)
            return FakeModel(nan=device.type != "cpu")

        monkeypatch.setattr(engine, "_load_on", fake_load)
        progress: list[float] = []
        engine.load(on_progress=progress.append)
        assert loads == ["mps", "cpu"]
        assert engine.loaded and engine.device.type == "cpu"
        assert engine.device_label == "CPU"
        assert progress[-1] == 1.0

    def test_cpu_failure_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(asr_engine, "ensure_model", lambda name, on_progress=None, cancel=None: tmp_path)
        engine = GigaAMEngine(device="cpu")
        monkeypatch.setattr(engine, "_load_on", lambda device, root: FakeModel(nan=True))
        with pytest.raises(ModelLoadError):
            engine.load()
        assert not engine.loaded

    def test_download_progress_scaled(self, monkeypatch, tmp_path):
        def fake_ensure(name, on_progress=None, cancel=None):
            on_progress(50, 100)
            on_progress(100, 100)
            return tmp_path

        monkeypatch.setattr(asr_engine, "ensure_model", fake_ensure)
        engine = GigaAMEngine(device="cpu")
        monkeypatch.setattr(engine, "_load_on", lambda device, root: FakeModel())
        progress: list[float] = []
        engine.load(on_progress=progress.append)
        assert progress[:2] == pytest.approx([0.45, 0.9])
        assert progress[-1] == 1.0

    def test_unload(self):
        engine = loaded_engine(FakeModel())
        engine.unload()
        assert not engine.loaded and engine.device is None

    def test_auto_policy(self, monkeypatch):
        monkeypatch.setattr(asr_engine, "pick_device", lambda pref: torch.device("mps"))
        engine = GigaAMEngine(device="auto")
        monkeypatch.setattr(asr_engine, "_AUTO_ALLOWS_MPS", False)
        assert engine._target_device().type == "cpu"
        monkeypatch.setattr(asr_engine, "_AUTO_ALLOWS_MPS", True)
        assert engine._target_device().type == "mps"
        assert GigaAMEngine(device="gpu")._target_device().type == "mps"


class TestTrustedCheckpoint:
    def test_skips_hash_only_with_marker(self, tmp_path):
        import gigaam

        name = "v3_e2e_rnnt"
        expected = gigaam._MODEL_HASHES[name]
        ckpt = tmp_path / f"{name}.ckpt"
        ckpt.write_bytes(b"not the model")
        other = tmp_path / "other.bin"
        other.write_bytes(b"x")
        original = gigaam.hash_path

        with asr_engine._trusted_checkpoint(name, str(tmp_path)):
            assert gigaam.hash_path(str(ckpt)) != expected  # маркера нет — честный md5
        (tmp_path / f"{name}.verified").write_text(expected, encoding="utf-8")
        with asr_engine._trusted_checkpoint(name, str(tmp_path)):
            assert gigaam.hash_path(str(ckpt)) == expected
            assert gigaam.hash_path(str(other)) != expected
        assert gigaam.hash_path is original

    def test_wrong_marker_ignored(self, tmp_path):
        import gigaam

        name = "v3_e2e_rnnt"
        (tmp_path / f"{name}.ckpt").write_bytes(b"x")
        (tmp_path / f"{name}.verified").write_text("deadbeef", encoding="utf-8")
        with asr_engine._trusted_checkpoint(name, str(tmp_path)):
            assert gigaam.hash_path(os.path.join(tmp_path, f"{name}.ckpt")) != gigaam._MODEL_HASHES[name]
