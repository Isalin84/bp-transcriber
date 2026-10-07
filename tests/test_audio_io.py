"""
Тесты для модуля audio_io (фикстуры генерируются ffmpeg «на лету»).
"""

import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from gigaam_transcriber import audio_io
from gigaam_transcriber.audio_io import (
    SAMPLE_RATE,
    decode,
    encode_preview,
    find_ffmpeg,
    probe_duration,
)
from gigaam_transcriber.exceptions import (
    AudioProcessingError,
    EmptyFileError,
    FFmpegNotFoundError,
    UnsupportedFormatError,
)
from gigaam_transcriber.progress import Cancelled, CancelToken

try:
    FFMPEG = find_ffmpeg()
except FFmpegNotFoundError:
    pytest.skip("FFmpeg не установлен", allow_module_level=True)

SINE = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]

# имя файла -> аргументы ffmpeg после входа
FORMATS = {
    "tone.mp3": ["-c:a", "libmp3lame"],
    "tone.m4a": ["-c:a", "aac"],
    "tone.opus": ["-c:a", "libopus"],
    "tone44k.wav": ["-ar", "44100", "-ac", "2"],
}


def run_ffmpeg(*args: str) -> None:
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    """Короткие медиафайлы (3 с) в разных контейнерах/кодеках."""
    root = tmp_path_factory.mktemp("media")
    files: dict[str, Path] = {}
    for name, args in FORMATS.items():
        files[name] = root / name
        run_ffmpeg(*SINE, *args, str(files[name]))
    files["video.mp4"] = root / "video.mp4"
    run_ffmpeg(
        *SINE, "-f", "lavfi", "-i", "testsrc=duration=3:size=64x48:rate=10",
        "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(files["video.mp4"]),
    )  # fmt: skip
    files["silent_video.mp4"] = root / "silent_video.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=10",
        "-c:v", "mpeg4", str(files["silent_video.mp4"]),
    )  # fmt: skip
    return files


@pytest.fixture(scope="module")
def long_audio(tmp_path_factory) -> Path:
    """Длинный (4 ч) моно 8 кГц файл: декодируется заведомо дольше 0.2 с."""
    path = tmp_path_factory.mktemp("long") / "long.wav"
    run_ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=8000:duration=14400",
        "-c:a", "pcm_u8", str(path),
    )  # fmt: skip
    return path


class TestDecode:
    @pytest.mark.parametrize("name", [*FORMATS, "video.mp4"])
    def test_decodes_mono_16k_int16(self, media, name):
        audio = decode(media[name])
        assert audio.pcm.dtype == np.int16
        assert audio.pcm.ndim == 1
        assert audio.duration == pytest.approx(3.0, abs=0.05)
        assert len(audio.pcm) == pytest.approx(3 * SAMPLE_RATE, abs=0.05 * SAMPLE_RATE)
        assert audio.source == media[name]
        assert np.abs(audio.pcm).max() > 1000  # синус не потерян

    def test_cancel_kills_process(self, long_audio, monkeypatch):
        spawned: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def spy(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(audio_io.subprocess, "Popen", spy)
        token = CancelToken()
        timer = threading.Timer(0.2, token.cancel)
        started = time.monotonic()
        timer.start()
        with pytest.raises(Cancelled):
            decode(long_audio, cancel=token)
        timer.join()
        assert time.monotonic() - started < 5
        assert len(spawned) == 1
        assert spawned[0].poll() is not None  # процесс завершён

    def test_video_without_audio_stream(self, media):
        with pytest.raises(AudioProcessingError, match="В файле нет аудиодорожки"):
            decode(media["silent_video.mp4"])

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.wav"
        empty.write_bytes(b"")
        with pytest.raises(EmptyFileError):
            decode(empty)

    def test_garbage_file(self, tmp_path):
        garbage = tmp_path / "garbage.mp3"
        garbage.write_bytes(b"this is definitely not audio" * 100)
        with pytest.raises(UnsupportedFormatError):
            decode(garbage)

    def test_stderr_not_in_user_message(self, tmp_path):
        garbage = tmp_path / "garbage.xyz"
        garbage.write_bytes(b"\x00\x01" * 1000)
        with pytest.raises((AudioProcessingError, UnsupportedFormatError)) as info:
            decode(garbage)
        assert "ffmpeg version" not in str(info.value).lower()
        assert "@" not in str(info.value)  # без «[mp3 @ 0x...]» из stderr


class TestEncodePreview:
    def test_roundtrip(self, media, tmp_path):
        audio = decode(media["tone44k.wav"])
        out = encode_preview(audio, tmp_path / "sub" / "preview.m4a")
        assert out == tmp_path / "sub" / "preview.m4a" and out.stat().st_size > 0
        assert not (tmp_path / "sub" / "preview.m4a.part").exists()
        again = decode(out)
        assert again.duration == pytest.approx(3.0, abs=0.1)
        assert probe_duration(out) == pytest.approx(3.0, abs=0.1)

