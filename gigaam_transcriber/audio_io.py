"""
Декодирование аудио/видео в память через один процесс ffmpeg.

Обеспечивает:
- поиск ffmpeg (встроенный в сборку, переменная BP_FFMPEG, PATH);
- декодирование любого медиафайла в моно int16 16 кГц с прогрессом и отменой;
- кодирование предпрослушивания (AAC .m4a) для плеера в UI.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable

import numpy as np

from .exceptions import (
    AudioProcessingError,
    EmptyFileError,
    FFmpegNotFoundError,
    FileNotFoundError,
    UnsupportedFormatError,
)
from .progress import CancelToken

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

_READ_CHUNK = 1 << 20  # размер блока чтения stdout ffmpeg
_WRITE_CHUNK = 1 << 20  # размер блока записи PCM в stdin ffmpeg
_POLL_INTERVAL = 0.05  # период проверки отмены, с
_PROBE_TIMEOUT = 30.0
_STDERR_TAIL_LINES = 5
_PREVIEW_BITRATE = "48k"

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_NO_STREAM_MARKERS = ("does not contain any stream", "matches no streams")
_INVALID_DATA_MARKER = "invalid data found when processing input"


@dataclass
class DecodedAudio:
    """Декодированное аудио: моно, 16 кГц, int16."""

    pcm: np.ndarray
    source: Path

    @property
    def duration(self) -> float:
        """Длительность в секундах."""
        return len(self.pcm) / SAMPLE_RATE

    def float32(self, start: float = 0.0, end: float | None = None) -> np.ndarray:
        """
        Копия фрагмента [start, end) секунд в диапазоне [-1, 1].

        Args:
            start: Начало фрагмента, секунды
            end: Конец фрагмента, секунды (None — до конца)
        """
        first = max(0, int(round(start * SAMPLE_RATE)))
        last = len(self.pcm) if end is None else int(round(end * SAMPLE_RATE))
        return self.pcm[first:last].astype(np.float32) / 32768.0

    def torch_waveform(self) -> "torch.Tensor":
        """Волна float32 формы (1, n) для pyannote."""
        import torch

        return torch.from_numpy(self.float32()).unsqueeze(0)


def find_ffmpeg() -> str:
    """
    Найти исполняемый файл ffmpeg.

    Порядок: встроенный в frozen-сборку, переменная BP_FFMPEG, PATH.

    Raises:
        FFmpegNotFoundError: если ffmpeg не найден
    """
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "bin" / exe
        if bundled.is_file():
            return str(bundled)
    from_env = os.environ.get("BP_FFMPEG")
    if from_env and Path(from_env).is_file():
        return from_env
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FFmpegNotFoundError()


def _popen_kwargs() -> dict[str, Any]:
    """Общие параметры запуска ffmpeg (без окна консоли на Windows)."""
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stderr": subprocess.PIPE}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _parse_duration(line: str) -> float | None:
    """Достать длительность из строки ``Duration: HH:MM:SS.xx`` баннера ffmpeg."""
    match = _DURATION_RE.search(line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


class _StderrReader(threading.Thread):
    """
    Непрерывно читает stderr ffmpeg (иначе процесс блокируется на переполнении пайпа).

    Извлекает длительность из баннера и позицию из ``-progress``;
    остальные строки хранит в коротком хвосте для лога.
    """

    def __init__(self, stream: IO[bytes]) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self.duration: float | None = None
        self.position: float = 0.0
        self.tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

    def run(self) -> None:
        for raw in self._stream:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            key, sep, value = line.partition("=")
            if sep and key in ("out_time_us", "out_time_ms"):
                if value.isdigit():
                    self.position = int(value) / 1_000_000
            elif sep and key.isidentifier() and " " not in line:
                continue  # прочие строки блока -progress (frame=, speed=, ...)
            else:
                if self.duration is None:
                    self.duration = _parse_duration(line)
                self.tail.append(line)


def probe_duration(path: Path | str) -> float | None:
    """
    Длительность файла по баннеру ffmpeg (без ffprobe).

    Returns:
        Секунды или None, если неизвестна.
    """
    cmd = [find_ffmpeg(), "-nostdin", "-hide_banner", "-i", str(path)]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, timeout=_PROBE_TIMEOUT, **_popen_kwargs()
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Не удалось определить длительность %s: %s", path, exc)
        return None
    for line in result.stderr.decode("utf-8", errors="replace").splitlines():
        duration = _parse_duration(line)
        if duration is not None:
            return duration
    return None


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Гарантированно остановить процесс ffmpeg."""
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def _raise_decode_error(path: Path, returncode: int, tail: list[str]) -> None:
    """Сопоставить сбой ffmpeg с исключением пакета (детали — только в лог)."""
    logger.error("ffmpeg завершился с кодом %s для %s: %s", returncode, path, " | ".join(tail))
    lowered = " ".join(tail).lower()
    if any(marker in lowered for marker in _NO_STREAM_MARKERS):
        raise AudioProcessingError("В файле нет аудиодорожки")
    if "no such file" in lowered:
        raise FileNotFoundError(str(path))
    if _INVALID_DATA_MARKER in lowered:
        raise UnsupportedFormatError(path.suffix or "?")
    raise AudioProcessingError("не удалось декодировать файл, подробности в журнале")


def decode(
    path: Path | str,
    *,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
) -> DecodedAudio:
    """
    Декодировать аудио/видео в моно int16 16 кГц одним процессом ffmpeg.

    Args:
        path: Путь к файлу
        on_progress: Callback с долей 0..1 (если длительность известна)
        cancel: Токен отмены (процесс ffmpeg убивается)

    Raises:
        FileNotFoundError, EmptyFileError, FFmpegNotFoundError,
        UnsupportedFormatError, AudioProcessingError, Cancelled
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.stat().st_size == 0:
        raise EmptyFileError(str(path))

    cmd = [
        find_ffmpeg(), "-nostdin", "-hide_banner", "-nostats",
        "-progress", "pipe:2", "-stats_period", "0.25",
        "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]  # fmt: skip
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, **_popen_kwargs())
    except OSError as exc:
        raise AudioProcessingError("не удалось запустить ffmpeg", cause=exc) from exc

    assert proc.stdout is not None and proc.stderr is not None
    buffer = bytearray()
    stderr_reader = _StderrReader(proc.stderr)
    stdout = proc.stdout

    def pump_stdout() -> None:
        while chunk := stdout.read1(_READ_CHUNK):
            buffer.extend(chunk)

    stdout_thread = threading.Thread(target=pump_stdout, daemon=True)
    stderr_reader.start()
    stdout_thread.start()

    last_reported = -1.0
    try:
        while stdout_thread.is_alive():
            stdout_thread.join(_POLL_INTERVAL)
            if cancel is not None:
                cancel.raise_if_cancelled()
            duration = stderr_reader.duration
            if on_progress and duration:
                fraction = min(stderr_reader.position / duration, 1.0)
                if fraction - last_reported >= 0.005:
                    last_reported = fraction
                    on_progress(fraction)
        proc.wait()
    finally:
        _kill(proc)
        stdout_thread.join()
        stderr_reader.join()
        stdout.close()
        proc.stderr.close()

    if proc.returncode != 0:
        _raise_decode_error(path, proc.returncode, list(stderr_reader.tail))
    usable = len(buffer) // 2 * 2
    if usable == 0:
        raise AudioProcessingError("В файле нет аудиодорожки")
    pcm = np.frombuffer(memoryview(buffer)[:usable], dtype=np.int16)
    if on_progress:
        on_progress(1.0)
    return DecodedAudio(pcm=pcm, source=path)


def encode_preview(
    audio: DecodedAudio,
    out_path: Path | str,
    *,
    cancel: CancelToken | None = None,
) -> Path:
    """
    Закодировать аудио в AAC 48 кбит/с (.m4a) для плеера в UI.

    Файл пишется во временное имя и атомарно переименовывается.

    Raises:
        FFmpegNotFoundError, AudioProcessingError, Cancelled
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_name(out_path.name + ".part")
    cmd = [
        find_ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-y",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
        "-c:a", "aac", "-b:a", _PREVIEW_BITRATE,
        "-movflags", "+faststart", "-f", "mp4", str(part_path),
    ]  # fmt: skip
    kwargs = _popen_kwargs()
    kwargs["stdin"] = subprocess.PIPE
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, **kwargs)
    except OSError as exc:
        raise AudioProcessingError("не удалось запустить ffmpeg", cause=exc) from exc

    assert proc.stdin is not None and proc.stderr is not None
    stderr_reader = _StderrReader(proc.stderr)
    stderr_reader.start()

    pcm_bytes = memoryview(audio.pcm).cast("B")
    try:
        try:
            for offset in range(0, len(pcm_bytes), _WRITE_CHUNK):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                proc.stdin.write(pcm_bytes[offset : offset + _WRITE_CHUNK])
            proc.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg завершился раньше; причина — в коде возврата
        while proc.poll() is None:
            if cancel is not None:
                cancel.raise_if_cancelled()
            stderr_reader.join(_POLL_INTERVAL)
        stderr_reader.join()
    except BaseException:
        _kill(proc)
        stderr_reader.join()
        with contextlib.suppress(OSError):
            proc.stdin.close()
        part_path.unlink(missing_ok=True)
        raise
    finally:
        proc.stderr.close()

    if proc.returncode != 0:
        part_path.unlink(missing_ok=True)
        logger.error("ffmpeg (preview) код %s: %s", proc.returncode, " | ".join(stderr_reader.tail))
        raise AudioProcessingError("не удалось создать файл предпрослушивания")
    os.replace(part_path, out_path)
    return out_path
