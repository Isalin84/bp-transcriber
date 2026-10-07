"""
Тесты GigaAMEngine на поддельной модели (без весов GigaAM).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from gigaam_transcriber import asr_engine
from gigaam_transcriber.asr_engine import GigaAMEngine, _padded_spans
from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
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

