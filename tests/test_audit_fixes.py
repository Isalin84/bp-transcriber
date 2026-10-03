"""
Тесты исправлений по результатам аудита.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from gigaam_transcriber import (
    AudioProcessingError,
    AudioProcessor,
    GigaAMTranscriber,
    TranscriptionResult,
    TranscriptionSegment,
    audio_processor,
)


def _result(speakers: list[str | None]) -> TranscriptionResult:
    segments = [
        TranscriptionSegment(text=f"s{i}", start=float(i), end=i + 1.0, speaker=speaker)
        for i, speaker in enumerate(speakers)
    ]
    return TranscriptionResult(
        text="",
        segments=segments,
        duration=float(len(segments)),
        language="ru",
        model_name="test",
        processing_time=0.0,
    )


class TestGetSpeakersOrder:
    def test_first_appearance_order(self):
        result = _result(["Спикер 2", "Спикер 10", "Спикер 1", "Спикер 2", None])
        assert result.get_speakers() == ["Спикер 2", "Спикер 10", "Спикер 1"]

    def test_no_speakers(self):
        assert _result([None, None]).get_speakers() == []


class TestBatchOutputExtension:
    @pytest.mark.parametrize("output_format", ["txt", "json", "srt", "vtt"])
    def test_extension_follows_format(self, tmp_path, output_format):
        transcriber = GigaAMTranscriber()
        captured: list[Path] = []

        def fake_transcribe(input_path, output_path=None, **kwargs):
            captured.append(output_path)
            assert kwargs["output_format"] == output_format
            return _result(["Спикер 1"])

        with patch.object(transcriber, "transcribe", side_effect=fake_transcribe):
            transcriber.transcribe_batch(
                [tmp_path / "talk.wav"], output_dir=tmp_path / "out", output_format=output_format
            )

        assert captured == [tmp_path / "out" / f"talk.{output_format}"]


class TestTranscribeStreamResultObject:
    """model.transcribe() может вернуть строку или объект с ``.text``."""

    @staticmethod
    def _run(answers: list) -> list[str]:
        transcriber = GigaAMTranscriber()
        transcriber._model = Mock()
        transcriber._model.transcribe.side_effect = answers
        audio = iter([np.zeros(21 * 16000, dtype=np.float32)])
        with patch.dict(sys.modules, {"torchaudio": Mock()}):
            return [s.text for s in transcriber.transcribe_stream(audio, 16000, 20.0)]

    def test_object_with_text(self):
        assert self._run([Mock(text=" привет "), Mock(text="мир")]) == ["привет", "мир"]

    def test_plain_string(self):
        assert self._run([" привет ", "мир"]) == ["привет", "мир"]


class TestTempFileCleanup:
    """Временный wav, созданный mkstemp, не должен оставаться после сбоя ffmpeg."""

    @pytest.fixture
    def created(self, monkeypatch) -> list[str]:
        names: list[str] = []
        real_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            names.append(name)
            return fd, name

        monkeypatch.setattr(tempfile, "mkstemp", tracking_mkstemp)
        failing = Mock(side_effect=subprocess.CalledProcessError(1, "ffmpeg", stderr="boom"))
        monkeypatch.setattr(audio_processor.subprocess, "run", failing)
        return names

    def test_normalize(self, tmp_path, created):
        source = tmp_path / "bad.wav"
        source.write_bytes(b"not audio")
        with pytest.raises(AudioProcessingError):
            AudioProcessor(ffmpeg_path="ffmpeg").normalize(source)
        assert len(created) == 1
        assert not Path(created[0]).exists()

    def test_extract_audio_from_video(self, tmp_path, created):
        source = tmp_path / "bad.mp4"
        source.write_bytes(b"not video")
        with pytest.raises(AudioProcessingError):
            AudioProcessor(ffmpeg_path="ffmpeg").extract_audio_from_video(source)
        assert len(created) == 1
        assert not Path(created[0]).exists()

    def test_explicit_output_is_kept(self, tmp_path, created):
        source = tmp_path / "bad.wav"
        source.write_bytes(b"not audio")
        out = tmp_path / "out.wav"
        out.write_bytes(b"")
        with pytest.raises(AudioProcessingError):
            AudioProcessor(ffmpeg_path="ffmpeg").normalize(source, out)
        assert out.exists() and not created
