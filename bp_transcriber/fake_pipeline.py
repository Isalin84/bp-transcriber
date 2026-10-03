"""Имитация ``TranscriptionPipeline`` для разработки UI и тестов.

Интерфейс совпадает с контрактом §1.8: ``set_token``, ``set_device``, ``prepare``,
``run``, ``close``. Этапы имитируются паузами, результат — правдоподобный
``TranscriptionResult`` (2 спикера, слова с таймингами, русский текст).
Если в имени файла есть ``fail`` — имитируется ошибка декодирования.

Типы ``ProgressEvent``/``CancelToken``/``Cancelled`` берутся из
``gigaam_transcriber.progress``; минимальные заменители определяются, только если
этот модуль ещё не существует.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment, WordSegment
from gigaam_transcriber.exceptions import AudioProcessingError, TranscriberError

logger = logging.getLogger(__name__)

try:
    from gigaam_transcriber.progress import STAGES, CancelToken, Cancelled, ProgressEvent
except ImportError:  # трек A1 ещё не влит
    STAGES = ("load", "decode", "vad", "asr", "diarize", "finalize")

    @dataclass
    class ProgressEvent:  # type: ignore[no-redef]
        stage: str
        stage_progress: float
        progress: float
        message: str
        eta_s: float | None = None

    class Cancelled(TranscriberError):  # type: ignore[no-redef]
        pass

    class CancelToken:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._event = threading.Event()

        def cancel(self) -> None:
            self._event.set()

        @property
        def cancelled(self) -> bool:
            return self._event.is_set()

        def raise_if_cancelled(self) -> None:
            if self._event.is_set():
                raise Cancelled("Отменено пользователем")


try:
    from gigaam_transcriber.pipeline import PipelineOptions
except ImportError:

    @dataclass
    class PipelineOptions:  # type: ignore[no-redef]
        diarization: str = "auto"
        num_speakers: int | None = None
        min_speakers: int | None = None
        max_speakers: int | None = None
        preview_path: Path | None = None


ProgressCallback = Callable[[ProgressEvent], None]

_WEIGHTS = {"decode": 0.05, "vad": 0.05, "asr": 0.60, "diarize": 0.30, "finalize": 0.0}
_STEPS = {"load": 10, "decode": 8, "vad": 5, "asr": 30, "diarize": 15, "finalize": 2}
_MESSAGES = {
    "load": "Загрузка модели GigaAM…",
    "decode": "Декодирование аудио…",
    "vad": "Поиск речи…",
    "asr": "Распознавание речи…",
    "diarize": "Определение спикеров…",
    "finalize": "Подготовка результата…",
}

_PHRASES = [
    "Добрый день, коллеги, давайте начнём нашу встречу.",
    "Сегодня обсудим итоги квартала и планы на следующий месяц.",
    "По продажам мы перевыполнили план примерно на двенадцать процентов.",
    "Отлично, а что с запуском нового продукта?",
    "Запуск переносится на две недели, ждём финальное согласование.",
    "Нужно ещё раз проверить бюджет на маркетинг.",
    "Я подготовлю сводку и разошлю её всем до пятницы.",
    "Есть вопросы по срокам поставки оборудования.",
    "Поставщик подтвердил, что отгрузка будет в начале месяца.",
    "Хорошо, тогда зафиксируем это в протоколе.",
    "Предлагаю провести отдельную встречу по найму.",
    "Согласен, давайте назначим её на среду.",
    "Также напоминаю про обучение по охране труда.",
    "Список участников я уже отправила в общий чат.",
    "Спасибо, по моей части вопросов больше нет.",
    "Тогда на этом всё, всем хорошего дня.",
]


def _find_ffmpeg() -> str | None:
    try:
        from gigaam_transcriber.audio_io import find_ffmpeg

        return find_ffmpeg()
    except Exception:  # noqa: BLE001 — модуля может ещё не быть или ffmpeg не найден
        pass
    from . import paths

    for name in ("ffmpeg.exe", "ffmpeg"):
        candidate = paths.bin_dir() / name
        if candidate.is_file():
            return str(candidate)
    return os.environ.get("BP_FFMPEG") or shutil.which("ffmpeg")


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _probe_duration(path: Path, ffmpeg: str | None) -> float | None:
    try:
        from gigaam_transcriber.audio_io import probe_duration

        return probe_duration(path)
    except Exception:  # noqa: BLE001
        pass
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-i", str(path)],
            capture_output=True,
            timeout=15,
            creationflags=_creationflags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


class FakePipeline:
    """Имитация TranscriptionPipeline (см. контракт §1.8)."""

    is_fake = True

    def __init__(
        self,
        model_name: str = "v3_e2e_rnnt",
        device: str = "auto",
        hf_token: str | None = None,
        *,
        step_delay: float | None = None,
        make_preview: bool = True,
    ):
        self.model_name = model_name
        self.device = device
        self.hf_token = hf_token
        self.step_delay = (
            float(os.environ.get("BP_FAKE_STEP", "0.08")) if step_delay is None else step_delay
        )
        self.make_preview = make_preview
        self._loaded = False
        self.runs = 0

    def set_token(self, token: str | None) -> None:
        self.hf_token = token

    def set_device(self, device: str) -> None:
        if device != self.device:
            self.device = device
            self._loaded = False

    def prepare(self, *, on_event: ProgressCallback | None = None, cancel: Any = None) -> None:
        if self._loaded:
            return
        self._simulate("load", {"load": 1.0}, 0.0, on_event, cancel)
        self._loaded = True

    def run(
        self,
        path: str | Path,
        options: Any,
        *,
        on_event: ProgressCallback | None = None,
        cancel: Any = None,
    ) -> TranscriptionResult:
        started = time.monotonic()
        path = Path(path)
        self.prepare(on_event=on_event, cancel=cancel)
        if not path.is_file():
            raise AudioProcessingError("файл не найден", file_path=str(path))

        mode = getattr(options, "diarization", "auto") or "auto"
        if mode == "auto":
            mode = "pyannote" if self.hf_token else "hybrid"
        stages = ["decode", "vad", "asr"] + (["diarize"] if mode != "none" else []) + ["finalize"]
        total_w = sum(_WEIGHTS[s] for s in stages) or 1.0
        weights = {s: _WEIGHTS[s] / total_w for s in stages}

        ffmpeg = _find_ffmpeg()
        timings: dict[str, float] = {}
        done = 0.0
        for stage in stages:
            t0 = time.monotonic()
            if stage == "decode" and "fail" in path.stem.lower():
                self._simulate(stage, weights, done, on_event, cancel, upto=0.4)
                raise AudioProcessingError(
                    "файл повреждён или имеет неподдерживаемый формат (имитация)",
                    file_path=path.name,
                )
            self._simulate(stage, weights, done, on_event, cancel)
            if stage == "decode":
                preview = getattr(options, "preview_path", None)
                if preview and self.make_preview and ffmpeg:
                    self._encode_preview(ffmpeg, path, Path(preview), cancel)
            done += weights[stage]
            timings[stage] = round(time.monotonic() - t0, 3)

        duration = _probe_duration(path, ffmpeg) or 95.0
        num_speakers = getattr(options, "num_speakers", None) or 2
        segments = self._make_segments(path.name, duration, mode != "none", num_speakers)
        self.runs += 1
        return TranscriptionResult(
            text=" ".join(s.text for s in segments),
            segments=segments,
            duration=duration,
            language="ru",
            model_name=self.model_name,
            processing_time=round(time.monotonic() - started, 3),
            metadata={
                "source": str(path),
                "device": "CPU (имитация)" if self.device == "cpu" else "GPU (имитация)",
                "diarization": mode,
                "num_speakers": len({s.speaker for s in segments if s.speaker}),
                "pipeline_version": 2,
                "timings": timings,
                "fake": True,
            },
        )

    def close(self) -> None:
        self._loaded = False

    # --- имитация ---------------------------------------------------------------

    def _simulate(
        self,
        stage: str,
        weights: dict[str, float],
        done: float,
        on_event: ProgressCallback | None,
        cancel: Any,
        upto: float = 1.0,
    ) -> None:
        steps = _STEPS[stage]
        last = max(1, int(steps * upto))
        for i in range(1, last + 1):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if self.step_delay:
                time.sleep(self.step_delay)
            frac = i / steps
            if on_event is not None:
                on_event(
                    ProgressEvent(
                        stage=stage,
                        stage_progress=frac,
                        progress=0.0 if stage == "load" else min(1.0, done + weights[stage] * frac),
                        message=_MESSAGES[stage],
                        eta_s=round((steps - i) * self.step_delay, 2) if self.step_delay else None,
                    )
                )

    def _encode_preview(self, ffmpeg: str, src: Path, dst: Path, cancel: Any) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
               "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart", str(dst)]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_creationflags()
            )
        except OSError as exc:
            logger.warning("Не удалось запустить ffmpeg для превью: %s", exc)
            return
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.cancelled:
                    proc.kill()
                    proc.wait()
                    cancel.raise_if_cancelled()
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.kill()
        if proc.returncode != 0:
            logger.warning("ffmpeg не смог сделать превью (код %s)", proc.returncode)
            dst.unlink(missing_ok=True)

    @staticmethod
    def _make_segments(
        name: str, duration: float, diarize: bool, num_speakers: int
    ) -> list[TranscriptionSegment]:
        rng = random.Random(name)
        segments: list[TranscriptionSegment] = []
        speakers = [f"Спикер {i + 1}" for i in range(max(1, min(num_speakers, 4)))]
        t = 0.6
        phrase_i = rng.randrange(len(_PHRASES))
        speaker_i = 0
        turn_left = rng.randint(1, 3)
        max_segments = 400
        while t < duration - 1.0 and len(segments) < max_segments:
            text = _PHRASES[phrase_i % len(_PHRASES)]
            phrase_i += 1
            words_text = text.split()
            seg_len = min(len(words_text) * rng.uniform(0.32, 0.45), duration - t)
            start, end = t, t + seg_len
            step = seg_len / len(words_text)
            words = [
                WordSegment(word=w, start=round(start + k * step, 3), end=round(start + (k + 0.85) * step, 3))
                for k, w in enumerate(words_text)
            ]
            segments.append(
                TranscriptionSegment(
                    text=text,
                    start=round(start, 3),
                    end=round(end, 3),
                    speaker=speakers[speaker_i] if diarize else None,
                    words=words,
                )
            )
            turn_left -= 1
            if turn_left <= 0:
                speaker_i = (speaker_i + 1) % len(speakers)
                turn_left = rng.randint(1, 3)
            t = end + rng.uniform(0.3, 1.6)
        return segments
