"""
Конвейер транскрипции: декодирование → VAD → ASR → диаризация → сегменты.

Держит загруженные модели между файлами и сообщает о прогрессе событиями
``ProgressEvent`` со взвешенным общим прогрессом и оценкой оставшегося времени.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import audio_io, vad
from .asr_engine import AsrSegment, GigaAMEngine
from .data_models import TranscriptionResult, TranscriptionSegment
from .diarization import (
    HybridDiarizer,
    PyannoteDiarizer,
    SpeakerTurn,
    assign_words,
    clear_caches,
    flatten_words,
    pyannote_cached,
    regroup,
    relabel,
)
from .exceptions import DiarizationError, EmptyAudioError
from .model_store import DEFAULT_MODEL
from .progress import Cancelled, CancelToken, ProgressCallback, ProgressEvent

logger = logging.getLogger(__name__)

PIPELINE_VERSION = 2
DIARIZATION_MODES = ("auto", "pyannote", "hybrid", "none")

_STAGE_WEIGHTS = {"decode": 0.05, "vad": 0.05, "asr": 0.60, "diarize": 0.30, "finalize": 0.0}
_STAGE_MESSAGES = {
    "load": "Загрузка модели",
    "decode": "Декодирование аудио",
    "vad": "Поиск речи",
    "asr": "Распознавание речи",
    "diarize": "Разделение по спикерам",
    "finalize": "Сохранение",
}
_EMIT_INTERVAL = 0.1  # не чаще 10 событий в секунду внутри этапа
_ETA_MIN_ELAPSED = 0.5
_ETA_MIN_FRACTION = 0.02


@dataclass
class PipelineOptions:
    """Параметры обработки одного файла."""

    diarization: str = "auto"  # "auto" | "pyannote" | "hybrid" | "none"
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    preview_path: Path | None = None  # если задан — сохранить туда AAC для плеера
    # Пересборка сегментов (см. diarization.regroup)
    max_gap: float = 1.0
    soft_max: float = 20.0
    hard_max: float = 40.0


class _Progress:
    """Взвешивание прогресса этапов и оценка оставшегося времени."""

    def __init__(self, on_event: ProgressCallback | None, stages: list[str]) -> None:
        self._on_event = on_event
        total = sum(_STAGE_WEIGHTS[s] for s in stages) or 1.0
        self._weights = {s: _STAGE_WEIGHTS[s] / total for s in stages}
        self._offsets: dict[str, float] = {}
        offset = 0.0
        for stage in stages:
            self._offsets[stage] = offset
            offset += self._weights[stage]
        self._stage = ""
        self._stage_started = 0.0
        self._last_emit = 0.0
        self._message = ""
        self.progress = 0.0

    def start(self, stage: str, message: str | None = None) -> None:
        """Начать этап."""
        self._stage = stage
        self._stage_started = time.monotonic()
        self._message = message or _STAGE_MESSAGES[stage]
        self.update(0.0, force=True)

    def message(self, text: str) -> None:
        """Сообщить текст (например, предупреждение) в рамках текущего этапа."""
        self._message = text
        self.update(None, force=True)

    def update(self, fraction: float | None, *, force: bool = False) -> None:
        """Обновить долю текущего этапа (None — оставить прежнюю)."""
        stage = self._stage
        weight = self._weights.get(stage, 0.0)
        offset = self._offsets.get(stage, 0.0)
        if fraction is None:
            fraction = (self.progress - offset) / weight if weight else 0.0
        fraction = min(max(fraction, 0.0), 1.0)
        self.progress = max(self.progress, min(offset + weight * fraction, 1.0))
        now = time.monotonic()
        if not force and fraction < 1.0 and now - self._last_emit < _EMIT_INTERVAL:
            return
        self._last_emit = now
        if self._on_event is None:
            return
        self._on_event(
            ProgressEvent(
                stage=stage,
                stage_progress=fraction,
                progress=self.progress,
                message=self._message,
                eta_s=self._eta(fraction, weight, now),
            )
        )

    def _eta(self, fraction: float, weight: float, now: float) -> float | None:
        """Оставшееся время по скорости текущего этапа."""
        elapsed = now - self._stage_started
        if weight <= 0 or fraction < _ETA_MIN_FRACTION or elapsed < _ETA_MIN_ELAPSED:
            return None
        rate = weight * fraction / elapsed  # доля общего прогресса в секунду
        return max((1.0 - self.progress) / rate, 0.0)


class TranscriptionPipeline:
    """
    Полный конвейер транскрипции с моделями, которые остаются в памяти.

    ``run`` и ``prepare`` вызываются из одного рабочего потока (защищены
    блокировкой); ``set_token`` / ``set_device`` можно вызывать из любого
    потока — изменения применяются при следующем запуске.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "auto", hf_token: str | None = None) -> None:
        """
        Args:
            model_name: Имя модели GigaAM
            device: "auto" | "cpu" | "gpu"
            hf_token: HF-токен для pyannote (может быть None)
        """
        self.model_name = model_name
        self._run_lock = threading.RLock()
        self._config_lock = threading.Lock()
        self._device = device
        self._token = hf_token or None
        self._engine = GigaAMEngine(model_name, device)
        self._pyannote: PyannoteDiarizer | None = None
        self._hybrid: HybridDiarizer | None = None
        self._pending_device: str | None = None
        self._pending_token = False

    # ---------------------------------------------------------------- настройки

    @property
    def engine(self) -> GigaAMEngine:
        """Движок ASR."""
        return self._engine

    def set_token(self, token: str | None) -> None:
        """Сменить HF-токен (применится при следующем запуске)."""
        with self._config_lock:
            self._token = token or None
            self._pending_token = True

    def set_device(self, device: str) -> None:
        """Сменить устройство; модель перезагрузится при следующем запуске."""
        with self._config_lock:
            if device != self._device:
                self._device = device
                self._pending_device = device

    def _apply_settings(self) -> None:
        """Применить отложенные изменения настроек (под _run_lock)."""
        with self._config_lock:
            device, self._pending_device = self._pending_device, None
            token_changed, self._pending_token = self._pending_token, False
        if device is not None:
            self._engine.unload()
            self._engine = GigaAMEngine(self.model_name, device)
            self._pyannote = None
            self._hybrid = None
        if token_changed:
            self._pyannote = None

    # ----------------------------------------------------------------- загрузка

    def prepare(self, *, on_event: ProgressCallback | None = None, cancel: CancelToken | None = None) -> None:
        """Загрузить GigaAM (этап "load")."""
        with self._run_lock:
            self._apply_settings()
            self._load_engine(on_event, cancel)

    def _load_engine(self, on_event: ProgressCallback | None, cancel: CancelToken | None) -> float:
        """Загрузить движок, если нужно; вернуть затраченное время."""
        if self._engine.loaded:
            return 0.0
        started = time.monotonic()
        last = [0.0]

        def emit(fraction: float, force: bool = False) -> None:
            now = time.monotonic()
            if on_event is None or (not force and fraction < 1.0 and now - last[0] < _EMIT_INTERVAL):
                return
            last[0] = now
            on_event(ProgressEvent("load", fraction, 0.0, _STAGE_MESSAGES["load"]))

        emit(0.0, force=True)
        self._engine.load(on_progress=emit, cancel=cancel)
        emit(1.0, force=True)
        return time.monotonic() - started

    def close(self) -> None:
        """Выгрузить все модели."""
        with self._run_lock:
            self._engine.unload()
            self._pyannote = None
            self._hybrid = None
            clear_caches()

    # ----------------------------------------------------------------- обработка

    def run(
        self,
        path: Path | str,
        options: PipelineOptions | None = None,
        *,
        on_event: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        """
        Транскрибировать файл.

        Raises:
            Cancelled: отменено пользователем
            EmptyAudioError: в файле нет речи
            TranscriberError: прочие ошибки (декодирование, модель)
        """
        options = options or PipelineOptions()
        if options.diarization not in DIARIZATION_MODES:
            raise ValueError(f"Неизвестный режим диаризации: {options.diarization}")
        path = Path(path)
        with self._run_lock:
            self._apply_settings()
            started = time.monotonic()
            timings: dict[str, float] = {}
            warnings_list: list[str] = []

            load_time = self._load_engine(on_event, cancel)
            if load_time:
                timings["load"] = round(load_time, 3)
            run_started = time.monotonic()

            stages = ["decode", "vad", "asr"]
            if options.diarization != "none":
                stages.append("diarize")
            stages.append("finalize")
            progress = _Progress(on_event, stages)

            def timed(stage: str) -> Callable[[], None]:
                stage_started = time.monotonic()
                progress.start(stage)

                def finish() -> None:
                    progress.update(1.0, force=True)
                    timings[stage] = round(time.monotonic() - stage_started, 3)

                return finish

            finish = timed("decode")
            audio = audio_io.decode(path, on_progress=progress.update, cancel=cancel)
            finish()

            finish = timed("vad")
            speech = vad.detect_speech(audio, on_progress=progress.update, cancel=cancel)
            chunks = vad.make_chunks(speech)
            finish()
            if not chunks:
                raise EmptyAudioError(str(path))

            finish = timed("asr")
            asr = self._engine.transcribe(audio, chunks, on_progress=progress.update, cancel=cancel)
            finish()
            if not asr:
                raise EmptyAudioError(str(path))

            turns: list[SpeakerTurn] | None = None
            mode = "none"
            diar_model: str | None = None
            if options.diarization != "none":
                finish = timed("diarize")
                turns, mode, diar_model = self._diarize(audio, speech, options, progress, warnings_list, cancel)
                finish()

            finish = timed("finalize")
            segments = self._build_segments(asr, turns, options)
            if options.preview_path is not None:
                try:
                    audio_io.encode_preview(audio, options.preview_path, cancel=cancel)
                except Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - без плеера транскрипт всё равно полезен
                    logger.warning("Не удалось создать предпрослушивание: %s", exc)
                    warnings_list.append("Не удалось подготовить аудио для плеера")
            finish()

            speakers = {s.speaker for s in segments if s.speaker}
            metadata: dict[str, object] = {
                "source": str(path),
                "device": self._engine.device_label,
                "diarization": mode,
                "num_speakers": len(speakers),
                "pipeline_version": PIPELINE_VERSION,
                "timings": timings,
            }
            if diar_model:
                metadata["diarization_model"] = diar_model
            if warnings_list:
                metadata["warnings"] = warnings_list
            logger.info(
                "Транскрипция %s: %.1f с аудио за %.1f с, сегментов %d, спикеров %d",
                path.name, audio.duration, time.monotonic() - run_started, len(segments), len(speakers),
            )
            return TranscriptionResult(
                text=" ".join(s.text for s in segments),
                segments=segments,
                duration=audio.duration,
                language="ru",
                model_name=self.model_name,
                processing_time=time.monotonic() - started,
                metadata=metadata,
            )

    def _diarize(
        self,
        audio: audio_io.DecodedAudio,
        speech: list[tuple[float, float]],
        options: PipelineOptions,
        progress: _Progress,
        warnings_list: list[str],
        cancel: CancelToken | None,
    ) -> tuple[list[SpeakerTurn] | None, str, str | None]:
        """
        Выбрать режим и выполнить диаризацию.

        Returns:
            (реплики или None, фактический режим, модель)
        """
        mode = options.diarization
        with self._config_lock:
            token = self._token
        if mode == "auto":
            mode = "pyannote" if token or pyannote_cached() else "hybrid"

        if mode == "pyannote":
            if self._pyannote is None:
                self._pyannote = PyannoteDiarizer(token, self._device)
            try:
                turns = self._pyannote.diarize(
                    audio,
                    num_speakers=options.num_speakers,
                    min_speakers=options.min_speakers,
                    max_speakers=options.max_speakers,
                    on_progress=progress.update,
                    cancel=cancel,
                )
                return turns, "pyannote", self._pyannote.model_id
            except Cancelled:
                raise
            except DiarizationError as exc:
                logger.warning("pyannote недоступен, используется гибридная диаризация: %s", exc)
                text = "pyannote недоступен, используется упрощённое разделение по спикерам"
                warnings_list.append(text)
                progress.message(text)
                mode = "hybrid"

        if self._hybrid is None:
            self._hybrid = HybridDiarizer(self._device)
        try:
            turns = self._hybrid.diarize(
                audio, speech, num_speakers=options.num_speakers, on_progress=progress.update, cancel=cancel
            )
            return turns, "hybrid", self._hybrid.model_kind
        except Cancelled:
            raise
        except DiarizationError as exc:
            logger.warning("Гибридная диаризация недоступна: %s", exc)
            text = "Разделение по спикерам недоступно, текст сохранён без спикеров"
            warnings_list.append(text)
            progress.message(text)
            return None, "none", None

    @staticmethod
    def _build_segments(
        asr: list[AsrSegment], turns: list[SpeakerTurn] | None, options: PipelineOptions
    ) -> list[TranscriptionSegment]:
        """Спикеры по словам → пересборка сегментов → «Спикер N»."""
        speakers = assign_words(flatten_words(asr), turns) if turns else None
        segments = regroup(
            asr,
            speakers,
            max_gap=options.max_gap,
            soft_max=options.soft_max,
            hard_max=options.hard_max,
        )
        relabel(segments)
        return segments
