"""
Детекция речи (Silero VAD) и нарезка речевых областей на чанки для ASR.

torch и silero_vad импортируются лениво; модель Silero (JIT) кэшируется
на процесс.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from .audio_io import SAMPLE_RATE, DecodedAudio
from .device import torch_threads
from .progress import CancelToken

_MODEL: Any = None
# Модель Silero хранит внутреннее состояние, поэтому один процесс = один вызов за раз.
_MODEL_LOCK = threading.Lock()

_MIN_SILENCE_MS = 300
_SPEECH_PAD_MS = 100
_MAX_SPEECH_S = 22.0
_TIME_RESOLUTION = 3  # знаков после запятой в секундах
_PROGRESS_STEP = 0.01


@dataclass
class Chunk:
    """Участок аудио для распознавания, секунды."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        """Длительность чанка в секундах."""
        return self.end - self.start


def _import_silero() -> Any:
    """
    Импортировать silero_vad, не меняя число потоков torch.

    ``silero_vad/model.py`` при импорте вызывает ``torch.set_num_threads(1)``,
    после чего весь CPU-инференс процесса (GigaAM, pyannote, speechbrain)
    шёл бы в один поток. Сохраняем и восстанавливаем значение.
    """
    import torch

    threads = torch.get_num_threads()
    try:
        import silero_vad
    finally:
        if torch.get_num_threads() != threads:
            torch.set_num_threads(threads)
    return silero_vad


def _get_model() -> Any:
    """Загрузить модель Silero VAD один раз на процесс (вызывать под _MODEL_LOCK)."""
    global _MODEL
    if _MODEL is None:
        _MODEL = _import_silero().load_silero_vad()
    return _MODEL


def detect_speech(
    audio: DecodedAudio,
    *,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
) -> list[tuple[float, float]]:
    """
    Найти участки речи.

    Args:
        audio: Декодированное аудио
        on_progress: Callback с долей 0..1
        cancel: Токен отмены (проверяется на каждом окне модели)

    Returns:
        Список (начало, конец) в секундах; ни одна область не длиннее 22 с.

    Raises:
        Cancelled: если запрошена отмена
    """
    if len(audio.pcm) == 0:
        return []

    import torch

    get_speech_timestamps = _import_silero().get_speech_timestamps
    last_reported = -1.0

    def on_silero_progress(percent: float) -> None:
        nonlocal last_reported
        if cancel is not None:
            cancel.raise_if_cancelled()
        fraction = percent / 100.0
        if on_progress and (fraction - last_reported >= _PROGRESS_STEP or fraction >= 1.0):
            last_reported = fraction
            on_progress(fraction)

    waveform = torch.from_numpy(audio.float32())
    # Silero быстрее в один поток (замер M4 Max: 1 поток 1.4 с, 10 — 6.5 с на 10.7 мин),
    # поэтому как и сам silero_vad ставим 1 поток, но только на время детекции.
    with _MODEL_LOCK, torch_threads(1):
        timestamps = get_speech_timestamps(
            waveform,
            _get_model(),
            sampling_rate=SAMPLE_RATE,
            return_seconds=True,
            time_resolution=_TIME_RESOLUTION,
            min_silence_duration_ms=_MIN_SILENCE_MS,
            speech_pad_ms=_SPEECH_PAD_MS,
            max_speech_duration_s=_MAX_SPEECH_S,
            progress_tracking_callback=on_silero_progress,
        )
    return [(float(t["start"]), float(t["end"])) for t in timestamps]


def _split_evenly(start: float, end: float, strict_limit: float) -> list[Chunk]:
    """Разбить область на равные части, каждая не длиннее strict_limit."""
    duration = end - start
    parts = int(duration / strict_limit) + 1 if duration > strict_limit else 1
    step = duration / parts
    bounds = [start + step * i for i in range(parts)] + [end]
    return [Chunk(bounds[i], bounds[i + 1]) for i in range(parts)]


def make_chunks(
    speech: list[tuple[float, float]],
    *,
    min_duration: float = 15.0,
    max_duration: float = 22.0,
    strict_limit: float = 25.0,
    min_keep: float = 0.2,
) -> list[Chunk]:
    """
    Склеить соседние речевые области в чанки для ASR.

    Порт логики GigaAM/gigaam/vad_utils.py (segment_audio_file): чанк закрывается,
    когда он длиннее min_duration или добавление следующей области превысит
    max_duration. Слишком длинные области делятся на равные части, поэтому ни
    один чанк не превышает strict_limit.

    Args:
        speech: Области речи (начало, конец) в секундах, по возрастанию
        min_duration: Целевая минимальная длительность чанка
        max_duration: Целевая максимальная длительность чанка
        strict_limit: Жёсткий предел длительности чанка
        min_keep: Хвост короче этого значения отбрасывается
    """
    chunks: list[Chunk] = []
    cur_start = cur_end = 0.0
    cur_duration = 0.0

    for start, end in speech:
        if cur_duration == 0.0:
            cur_start = start
        elif cur_duration > min_keep and (
            cur_duration + (end - cur_end) > max_duration or cur_duration > min_duration
        ):
            chunks.extend(_split_evenly(cur_start, cur_end, strict_limit))
            cur_start = start
        cur_end = end
        cur_duration = cur_end - cur_start

    if cur_duration > min_keep:
        chunks.extend(_split_evenly(cur_start, cur_end, strict_limit))
    return chunks
