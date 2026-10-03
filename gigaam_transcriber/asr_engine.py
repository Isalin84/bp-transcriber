"""
Движок распознавания речи GigaAM.

Единственный модуль пакета, который знает внутренности GigaAM
(``forward``, ``_decode``, формат слов). При обновлении GigaAM контракт
проверяет ``tests/test_asr_engine_contract.py``.

Обеспечивает:
- загрузку весов через ``model_store`` с прогрессом и отменой;
- smoke-тест модели на ускорителе с откатом на CPU;
- пакетное распознавание чанков с пословными таймингами в абсолютном времени.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import os
import threading
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator

import numpy as np

from .audio_io import SAMPLE_RATE, DecodedAudio
from .data_models import WordSegment
from .device import asr_cpu_threads, pick_device, torch_threads
from .device import device_label as _device_label
from .exceptions import ModelLoadError, TranscriberError
from .model_store import DEFAULT_MODEL, ensure_model
from .progress import CancelToken
from .vad import Chunk

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)

# Размер пакета чанков по типу устройства; переопределяется BP_ASR_BATCH.
_BATCH_SIZES = {"cpu": 4, "mps": 8, "cuda": 16}
_BATCH_ENV = "BP_ASR_BATCH"

# Политика "auto" для Apple Silicon. Замер scripts/bench.py (M4 Max, 10.7 мин речи):
# ASR на MPS 5.9-6.8 с против 9.1-9.4 с на CPU (1 поток, см. device.asr_cpu_threads),
# текст совпал пословно (difflib 1.0; единожды, на холодном кэше Metal, 0.997) —
# поэтому auto -> mps.
_AUTO_ALLOWS_MPS = True

_SMOKE_SECONDS = 2.0
_SMOKE_AMPLITUDE = 0.01
_MIN_CHUNK_SAMPLES = SAMPLE_RATE // 20  # 50 мс: короче энкодер не обрабатывает
# Чанк расширяется в тишину (не дальше середины паузы до соседа): VAD режет
# вплотную к речи, а запас помогает модели с последним словом и пунктуацией.
_CHUNK_PAD = 0.3
_LOAD_SHARE = 0.9  # доля прогресса загрузки, отведённая скачиванию весов
_TIME_DIGITS = 3

# gigaam.load_model патчится на время загрузки; не допускаем параллельных загрузок.
_LOAD_LOCK = threading.Lock()


@dataclass
class AsrSegment:
    """Результат распознавания одного чанка (время абсолютное, секунды)."""

    start: float
    end: float
    text: str
    words: list[WordSegment] = field(default_factory=list)


def _collate(wavs: list["torch.Tensor"]) -> tuple["torch.Tensor", "torch.Tensor"]:
    """Дополнить нулями до общей длины (как gigaam.utils.AudioDataset.collate)."""
    import torch

    lengths = torch.tensor([len(w) for w in wavs], dtype=torch.long)
    max_len = int(lengths.max().item())
    batch = torch.zeros(len(wavs), max_len, dtype=wavs[0].dtype)
    for i, wav in enumerate(wavs):
        batch[i, : wav.shape[-1]] = wav
    return batch, lengths


def _padded_spans(chunks: list[Chunk], duration: float) -> list[Chunk]:
    """
    Границы нарезки чанков с запасом _CHUNK_PAD в тишину.

    Запас не заходит дальше середины паузы до соседнего чанка, поэтому
    участки соседей не пересекаются и слова не дублируются.
    """
    order = sorted(range(len(chunks)), key=lambda i: chunks[i].start)
    spans: list[Chunk] = [Chunk(c.start, c.end) for c in chunks]
    for pos, i in enumerate(order):
        chunk = chunks[i]
        start = max(chunk.start - _CHUNK_PAD, 0.0)
        end = min(chunk.end + _CHUNK_PAD, duration)
        if pos > 0:
            prev = chunks[order[pos - 1]]
            start = max(start, min((prev.end + chunk.start) / 2, chunk.start))
        if pos + 1 < len(order):
            nxt = chunks[order[pos + 1]]
            end = min(end, max((chunk.end + nxt.start) / 2, chunk.end))
        spans[i] = Chunk(start, max(end, start))
    return spans


def _batch_size(device_type: str) -> int:
    """Размер пакета для устройства с учётом переменной BP_ASR_BATCH."""
    raw = os.environ.get(_BATCH_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logger.warning("Некорректное значение %s=%r, используется значение по умолчанию", _BATCH_ENV, raw)
        else:
            if value > 0:
                return value
            logger.warning("%s должно быть положительным, используется значение по умолчанию", _BATCH_ENV)
    return _BATCH_SIZES.get(device_type, _BATCH_SIZES["cpu"])


@contextlib.contextmanager
def _trusted_checkpoint(name: str, root: str) -> Iterator[None]:
    """
    Пропустить повторный md5 чекпоинта внутри ``gigaam.load_model``.

    ``model_store.ensure_model`` уже проверил md5 и оставил маркер
    ``<name>.verified``; GigaAM же читает файл (~450 МБ) в память целиком
    ради хеша. Подмена действует, только если маркер совпадает с эталоном.
    """
    import gigaam

    expected = gigaam._MODEL_HASHES.get(name)
    marker = os.path.join(root, f"{name}.verified")
    try:
        with open(marker, encoding="utf-8") as stream:
            verified = stream.read().strip() == expected
    except OSError:
        verified = False
    if not verified or expected is None:
        yield
        return

    original = gigaam.hash_path
    ckpt = os.path.normcase(os.path.abspath(os.path.join(root, f"{name}.ckpt")))

    def hash_path(path: str) -> str:
        if os.path.normcase(os.path.abspath(path)) == ckpt:
            return expected
        return original(path)

    gigaam.hash_path = hash_path
    try:
        yield
    finally:
        gigaam.hash_path = original


class GigaAMEngine:
    """
    Обёртка над моделью GigaAM ASR.

    Модель загружается явно (``load``) и остаётся в памяти между файлами.
    Методы не потокобезопасны: вызывать из одного рабочего потока.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "auto") -> None:
        """
        Args:
            model_name: Имя модели GigaAM (например, "v3_e2e_rnnt")
            device: "auto" | "cpu" | "gpu"
        """
        self.model_name = model_name
        self.device_pref = device
        self._model: Any = None
        self._device: "torch.device | None" = None

    # ------------------------------------------------------------------ свойства

    @property
    def loaded(self) -> bool:
        """Загружена ли модель."""
        return self._model is not None

    @property
    def model(self) -> Any:
        """Загруженная модель GigaAM (для устаревшего потокового API); загружает при необходимости."""
        if not self.loaded:
            self.load()
        return self._model

    @property
    def device(self) -> "torch.device | None":
        """Фактическое устройство загруженной модели."""
        return self._device

    @property
    def device_label(self) -> str:
        """Человекочитаемое название устройства (фактического или ожидаемого)."""
        if self._device is not None:
            return _device_label(self._device)
        return _device_label(self._target_device())

    # ------------------------------------------------------------------ загрузка

    def _target_device(self) -> "torch.device":
        """Устройство по предпочтению пользователя с учётом политики auto."""
        dev = pick_device(self.device_pref)
        if self.device_pref == "auto" and dev.type == "mps" and not _AUTO_ALLOWS_MPS:
            import torch

            return torch.device("cpu")
        return dev

    def load(
        self,
        *,
        on_progress: Callable[[float], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> None:
        """
        Скачать (при необходимости) и загрузить модель, проверить её smoke-тестом.

        Если на ускорителе smoke-тест падает или даёт NaN/Inf, модель
        перезагружается на CPU.

        Args:
            on_progress: Callback с долей 0..1
            cancel: Токен отмены (проверяется при скачивании и между шагами)

        Raises:
            ModelLoadError: не удалось скачать или загрузить модель
            Cancelled: загрузка отменена
        """
        if self.loaded:
            if on_progress:
                on_progress(1.0)
            return

        def on_bytes(done: int, total: int) -> None:
            if on_progress and total > 0:
                on_progress(min(done / total, 1.0) * _LOAD_SHARE)

        root = ensure_model(self.model_name, on_progress=on_bytes, cancel=cancel)
        if on_progress:
            on_progress(_LOAD_SHARE)
        if cancel is not None:
            cancel.raise_if_cancelled()

        target = self._target_device()
        model = self._load_on(target, str(root))
        if target.type != "cpu":
            reason = self._smoke_test(model)
            if reason is not None:
                logger.warning(
                    "GigaAM на %s не прошла проверку (%s), используется CPU", target, reason
                )
                del model
                self._free_memory(target)
                import torch

                target = torch.device("cpu")
                model = self._load_on(target, str(root))
        if target.type == "cpu":
            reason = self._smoke_test(model)
            if reason is not None:
                raise ModelLoadError(self.model_name, RuntimeError(reason))

        self._model = model
        self._device = target
        logger.info("Модель %s загружена на %s", self.model_name, _device_label(target))
        if on_progress:
            on_progress(1.0)

    def _load_on(self, device: "torch.device", root: str) -> Any:
        """Загрузить модель на устройство (fp16-энкодер на ускорителях)."""
        import gigaam

        try:
            with _LOAD_LOCK, _trusted_checkpoint(self.model_name, root):
                return gigaam.load_model(
                    self.model_name,
                    fp16_encoder=device.type != "cpu",
                    device=device,
                    download_root=root,
                )
        except Exception as exc:  # noqa: BLE001 - любые сбои загрузки -> ModelLoadError
            logger.exception("Не удалось загрузить GigaAM на %s", device)
            raise ModelLoadError(self.model_name, exc) from exc

    @staticmethod
    def _model_io(model: Any) -> tuple["torch.device", "torch.dtype"]:
        """Устройство и тип входа модели (как в GigaAM transcribe_longform)."""
        param = next(model.parameters())
        return param.device, param.dtype

    def _smoke_test(self, model: Any) -> str | None:
        """
        Прогнать 2 с шума через forward + _decode.

        Returns:
            None, если всё в порядке, иначе описание проблемы.
        """
        import torch

        rng = np.random.default_rng(0)
        long_len = int(_SMOKE_SECONDS * SAMPLE_RATE)
        wavs = [
            torch.from_numpy((rng.standard_normal(n) * _SMOKE_AMPLITUDE).astype(np.float32))
            for n in (long_len, long_len * 3 // 4)
        ]
        device, dtype = self._model_io(model)
        try:
            with torch.inference_mode(), warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="An output with one or more elements was resized")
                batch, lengths = _collate(wavs)
                encoded, encoded_len = model.forward(batch.to(device).to(dtype), lengths.to(device))
                if not bool(torch.isfinite(encoded).all()):
                    return "выход энкодера содержит NaN/Inf"
                decoded = model._decode(encoded, encoded_len, lengths.to(device), True)
                if len(decoded) != len(wavs):
                    return "декодер вернул неожиданное число результатов"
        except Exception as exc:  # noqa: BLE001 - любая ошибка = непригодное устройство
            logger.debug("Smoke-тест GigaAM упал", exc_info=True)
            return f"{type(exc).__name__}: {exc}"
        return None

    @staticmethod
    def _free_memory(device: "torch.device | None") -> None:
        """Освободить кэш памяти ускорителя."""
        gc.collect()
        if device is None:
            return
        import torch

        if device.type == "cuda":
            torch.cuda.empty_cache()
        elif device.type == "mps":
            with contextlib.suppress(Exception):
                torch.mps.empty_cache()

    def unload(self) -> None:
        """Выгрузить модель и освободить память."""
        device = self._device
        self._model = None
        self._device = None
        self._free_memory(device)

    # ------------------------------------------------------------- распознавание

    def transcribe(
        self,
        audio: DecodedAudio,
        chunks: list[Chunk],
        *,
        on_progress: Callable[[float], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> list[AsrSegment]:
        """
        Распознать чанки пакетами.

        Чанки сортируются по убыванию длины (меньше паддинга), результат
        возвращается в хронологическом порядке; чанки без текста отбрасываются.

        Args:
            audio: Декодированное аудио
            chunks: Чанки (из vad.make_chunks), каждый не длиннее 25 с
            on_progress: Callback с долей обработанных секунд аудио
            cancel: Токен отмены (проверяется между пакетами)

        Raises:
            Cancelled: распознавание отменено
            TranscriberError: сбой модели во время распознавания
        """
        if not self.loaded:
            self.load(cancel=cancel)
        import torch

        model = self._model
        device, dtype = self._model_io(model)
        batch_size = _batch_size(device.type)

        spans = _padded_spans(chunks, audio.duration)
        order = sorted(range(len(chunks)), key=lambda i: spans[i].duration, reverse=True)
        total = sum(max(c.duration, 0.0) for c in chunks)
        processed = 0.0
        results: list[AsrSegment | None] = [None] * len(chunks)
        threads = asr_cpu_threads() if device.type == "cpu" else None

        for first in range(0, len(order), batch_size):
            if cancel is not None:
                cancel.raise_if_cancelled()
            indices = order[first : first + batch_size]
            wavs: list[torch.Tensor] = []
            kept: list[int] = []
            for i in indices:
                samples = audio.float32(spans[i].start, spans[i].end)
                if len(samples) >= _MIN_CHUNK_SAMPLES:
                    wavs.append(torch.from_numpy(samples))
                    kept.append(i)
            if wavs:
                with torch_threads(threads):
                    batch_segments = self._run_batch(model, device, dtype, wavs, spans, kept)
                for i, segment in zip(kept, batch_segments):
                    results[i] = segment
            processed += sum(max(chunks[i].duration, 0.0) for i in indices)
            if on_progress and total > 0:
                on_progress(min(processed / total, 1.0))

        segments = [s for s in results if s is not None and s.text.strip()]
        segments.sort(key=lambda s: (s.start, s.end))
        if on_progress:
            on_progress(1.0)
        return segments

    def _run_batch(
        self,
        model: Any,
        device: "torch.device",
        dtype: "torch.dtype",
        wavs: list["torch.Tensor"],
        spans: list[Chunk],
        indices: list[int],
    ) -> list[AsrSegment]:
        """forward + _decode одного пакета; слова переводятся в абсолютное время."""
        import torch

        try:
            with torch.inference_mode(), warnings.catch_warnings():
                # torch.stft на MPS предупреждает о resize выходного тензора — безвредно
                warnings.filterwarnings("ignore", message="An output with one or more elements was resized")
                batch, lengths = _collate(wavs)
                lengths = lengths.to(device)
                encoded, encoded_len = model.forward(batch.to(device).to(dtype), lengths)
                decoded = model._decode(encoded, encoded_len, lengths, True)
        except Exception as exc:  # noqa: BLE001 - сбой модели -> понятная ошибка
            logger.exception("Сбой GigaAM на пакете из %d чанков", len(wavs))
            raise TranscriberError(f"Ошибка распознавания речи: {exc}") from exc

        segments: list[AsrSegment] = []
        for i, (text, words) in zip(indices, decoded):
            span = spans[i]
            segments.append(
                AsrSegment(
                    start=span.start,
                    end=span.end,
                    text=text.strip(),
                    words=[
                        WordSegment(
                            word=w.text,
                            start=round(span.start + max(w.start, 0.0), _TIME_DIGITS),
                            end=round(min(span.start + w.end, span.end), _TIME_DIGITS),
                        )
                        for w in words or []
                        if w.text
                    ],
                )
            )
        return segments
