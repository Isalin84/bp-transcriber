"""
Основной класс GigaAMTranscriber - фасад для работы с GigaAM.

Обеспечивает:
- Транскрипцию аудио и видео файлов любой длительности
- Опциональную диаризацию спикеров
- Различные форматы вывода

Вся обработка делегируется ``TranscriptionPipeline`` (pipeline.py).
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

import numpy as np

from .audio_processor import AudioProcessor
from .data_models import (
    DiarizationMode,
    OutputFormat,
    TranscriptionResult,
    TranscriptionSegment,
)
from .exceptions import EmptyFileError, UnsupportedFormatError
from .formatters import save_result
from .pipeline import PipelineOptions, TranscriptionPipeline

logger = logging.getLogger(__name__)

# Устройства CLI ("cuda") -> предпочтения конвейера ("gpu")
_DEVICE_PREFS = {"auto": "auto", "cpu": "cpu", "cuda": "gpu", "gpu": "gpu", "mps": "gpu"}


class GigaAMTranscriber:
    """
    Фасад для работы с GigaAM транскрипцией.

    Принципы:
    - Lazy loading моделей (загружаются при первом использовании)
    - Единообразный интерфейс для audio/video
    - Прозрачная обработка любой длительности
    - Graceful degradation: без HF_TOKEN pyannote заменяется гибридной диаризацией

    Примеры использования:

    >>> # Простая транскрипция
    >>> transcriber = GigaAMTranscriber()
    >>> result = transcriber.transcribe("audio.wav")
    >>> print(result.text)

    >>> # С диаризацией
    >>> result = transcriber.transcribe("meeting.mp4", diarization="pyannote")
    >>> for seg in result.segments:
    ...     print(f"{seg.speaker}: {seg.text}")

    >>> # Сохранение в файл
    >>> result.save("transcript.json", format="json")
    """

    def __init__(
        self,
        model_name: str = "v3_e2e_rnnt",
        device: str = "auto",
        hf_token: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        verbose: bool = False,
        fp16_encoder: bool = True,
    ):
        """
        Инициализация транскрибера.

        Args:
            model_name: Имя модели GigaAM ("v3_e2e_rnnt", "v3_e2e_ctc", и т.д.)
            device: Устройство ("auto", "cuda", "cpu")
            hf_token: HuggingFace токен для pyannote диаризации
            cache_dir: Директория для кэша
            verbose: Подробный вывод
            fp16_encoder: Оставлен для совместимости; на GPU энкодер всегда FP16,
                на CPU — FP32 (так решает движок ASR)
        """
        self.model_name = model_name
        self.device_pref = _DEVICE_PREFS.get(device, "auto")
        self.device = self._resolve_device(self.device_pref)
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "gigaam_transcriber"
        self.verbose = verbose
        self.fp16_encoder = fp16_encoder

        # Lazy-loaded компоненты
        self._pipeline: Optional[TranscriptionPipeline] = None

        # Создание директории кэша
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Настройка логирования
        if verbose:
            logging.basicConfig(level=logging.DEBUG)

        logger.info(f"GigaAMTranscriber инициализирован: model={model_name}, device={self.device}")

    @staticmethod
    def _resolve_device(pref: str) -> str:
        """Тип устройства для отображения ("cuda", "mps", "cpu")."""
        try:
            from .device import pick_device

            return pick_device(pref).type
        except ImportError:
            return "cpu"

    # =========================================================================
    # Свойства с ленивой загрузкой
    # =========================================================================

    @property
    def pipeline(self) -> TranscriptionPipeline:
        """Конвейер транскрипции (создаётся лениво, модели — при первом запуске)."""
        if self._pipeline is None:
            self._pipeline = TranscriptionPipeline(
                model_name=self.model_name,
                device=self.device_pref,
                hf_token=self.hf_token,
            )
        return self._pipeline

    @property
    def model(self):
        """Модель GigaAM (ленивая загрузка); нужна потоковому API."""
        return self.pipeline.engine.model

    # =========================================================================
    # Контекстный менеджер
    # =========================================================================

    def __enter__(self):
        """Вход в контекст."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекста - освобождение ресурсов."""
        self.cleanup()

    def cleanup(self):
        """Освобождение памяти моделей."""
        if self._pipeline is not None:
            self._pipeline.close()
        logger.info("Ресурсы освобождены")

    # =========================================================================
    # Валидация
    # =========================================================================

    def _validate_input(self, path: Path) -> None:
        """Валидация входного файла."""
        if not path.exists():
            raise FileNotFoundError(str(path))

        if path.stat().st_size == 0:
            raise EmptyFileError(str(path))

        if not AudioProcessor.is_supported_file(path):
            raise UnsupportedFormatError(path.suffix)

    # =========================================================================
    # Основные методы транскрипции
    # =========================================================================

    def transcribe(
        self,
        input_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        language: str = "ru",
        output_format: OutputFormat = "txt",
        merge_same_speaker: bool = True,
        min_segment_gap: float = 1.0,
    ) -> TranscriptionResult:
        """
        Универсальный метод транскрипции.

        Args:
            input_path: Путь к входному файлу (аудио или видео)
            output_path: Путь для сохранения результата (опционально)
            diarization: Режим диаризации ("none", "pyannote", "hybrid", "auto");
                если pyannote недоступен, используется "hybrid"
            num_speakers: Точное количество спикеров (если известно)
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
            language: Язык ("ru")
            output_format: Формат вывода ("txt", "json", "srt", "vtt")
            merge_same_speaker: Объединять фразы одного спикера в сегменты до ~20 с;
                False — сегмент на каждое предложение
            min_segment_gap: Пауза (секунды), после которой начинается новый сегмент

        Returns:
            TranscriptionResult с текстом, сегментами и метаданными
        """
        input_path = Path(input_path)
        self._validate_input(input_path)
        logger.info(f"Начало транскрипции: {input_path}")

        options = PipelineOptions(
            diarization=diarization,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            max_gap=min_segment_gap,
        )
        if not merge_same_speaker:
            options.soft_max = 0.0

        result = self.pipeline.run(input_path, options)
        result.language = language

        logger.info(
            f"Транскрипция завершена за {result.processing_time:.1f}с "
            f"({len(result.segments)} сегментов)"
        )

        if output_path:
            save_result(result, output_path, output_format)
            logger.info(f"Результат сохранён: {output_path}")

        return result

    # =========================================================================
    # Альтернативные методы
    # =========================================================================

    def audio2text(
        self,
        in_audio: Union[str, Path],
        out_text: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        **kwargs,
    ) -> TranscriptionResult:
        """
        Транскрибация аудио файла.

        Поддерживает: WAV, FLAC, MP3, OGG, M4A, AAC и любые ffmpeg-совместимые форматы.

        Args:
            in_audio: Путь к аудио файлу
            out_text: Путь для сохранения результата
            diarization: Режим диаризации
            **kwargs: Дополнительные параметры для transcribe()

        Returns:
            TranscriptionResult
        """
        return self.transcribe(
            in_audio,
            output_path=out_text,
            diarization=diarization,
            **kwargs,
        )

    def video2text(
        self,
        in_video: Union[str, Path],
        out_text: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        keep_temp_audio: bool = False,
        **kwargs,
    ) -> TranscriptionResult:
        """
        Транскрибация видео файла.

        Аудио декодируется ffmpeg прямо в память, временных файлов нет.

        Args:
            in_video: Путь к видео файлу
            out_text: Путь для сохранения результата
            diarization: Режим диаризации
            keep_temp_audio: Оставлен для совместимости (временного аудио больше нет)
            **kwargs: Дополнительные параметры для transcribe()

        Returns:
            TranscriptionResult
        """
        return self.transcribe(
            in_video,
            output_path=out_text,
            diarization=diarization,
            **kwargs,
        )

    def transcribe_batch(
        self,
        input_paths: List[Union[str, Path]],
        output_dir: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        n_workers: int = 1,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        output_format: OutputFormat = "txt",
        **kwargs,
    ) -> List[TranscriptionResult]:
        """
        Пакетная обработка нескольких файлов.

        Args:
            input_paths: Список путей к файлам
            output_dir: Директория для сохранения результатов
            diarization: Режим диаризации
            n_workers: Не используется (обработка последовательная), оставлен для совместимости
            progress_callback: Callback для прогресса: (current, total, filename)
            output_format: Формат файлов результата ("txt", "json", "srt", "vtt"),
                определяет и расширение выходного файла
            **kwargs: Дополнительные параметры

        Returns:
            Список TranscriptionResult
        """
        results = []
        total = len(input_paths)

        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        # Последовательная обработка (GPU не параллелится)
        for i, input_path in enumerate(input_paths):
            input_path = Path(input_path)

            if progress_callback:
                progress_callback(i, total, input_path.name)

            logger.info(f"Обработка {i+1}/{total}: {input_path.name}")

            # Определение выходного пути
            output_path = None
            if output_dir:
                output_path = output_dir / f"{input_path.stem}.{output_format}"

            try:
                result = self.transcribe(
                    input_path,
                    output_path=output_path,
                    diarization=diarization,
                    output_format=output_format,
                    **kwargs,
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Ошибка при обработке {input_path}: {e}")
                # Создаём пустой результат с ошибкой
                results.append(TranscriptionResult(
                    text="",
                    segments=[],
                    duration=0,
                    language="ru",
                    model_name=self.model_name,
                    processing_time=0,
                    metadata={"source": str(input_path), "error": str(e)},
                ))

        if progress_callback:
            progress_callback(total, total, "Готово")

        return results

    def transcribe_stream(
        self,
        audio_iterator: Iterator[np.ndarray],
        sample_rate: int = 16000,
        chunk_duration: float = 20.0,
    ) -> Iterator[TranscriptionSegment]:
        """
        Потоковая транскрипция для real-time приложений.

        Args:
            audio_iterator: Итератор numpy массивов с аудио данными
            sample_rate: Частота дискретизации
            chunk_duration: Длительность чанка в секундах

        Yields:
            TranscriptionSegment для каждого обработанного чанка
        """
        import torch

        buffer = []
        buffer_duration = 0
        current_time = 0
        chunk_samples = int(chunk_duration * sample_rate)

        for chunk in audio_iterator:
            buffer.append(chunk)
            buffer_duration += len(chunk) / sample_rate

            # Когда накопилось достаточно данных
            while buffer_duration >= chunk_duration:
                # Собираем чанк
                audio_data = np.concatenate(buffer)
                process_samples = min(chunk_samples, len(audio_data))
                process_chunk = audio_data[:process_samples]

                # Сохраняем остаток
                remaining = audio_data[process_samples:]
                buffer = [remaining] if len(remaining) > 0 else []
                buffer_duration = len(remaining) / sample_rate

                # Транскрибируем
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    temp_path = Path(f.name)

                try:
                    import torchaudio
                    waveform = torch.from_numpy(process_chunk).unsqueeze(0).float()
                    torchaudio.save(str(temp_path), waveform, sample_rate)

                    result = self.model.transcribe(str(temp_path))
                    text = getattr(result, "text", result)

                    if text and text.strip():
                        segment_duration = len(process_chunk) / sample_rate
                        yield TranscriptionSegment(
                            text=text.strip(),
                            start=current_time,
                            end=current_time + segment_duration,
                        )
                        current_time += segment_duration
                finally:
                    if temp_path.exists():
                        temp_path.unlink()

        # Обработка остатка
        if buffer and buffer_duration > 0.5:  # Минимум 0.5 сек
            audio_data = np.concatenate(buffer)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = Path(f.name)

            try:
                import torchaudio
                waveform = torch.from_numpy(audio_data).unsqueeze(0).float()
                torchaudio.save(str(temp_path), waveform, sample_rate)

                result = self.model.transcribe(str(temp_path))
                text = getattr(result, "text", result)

                if text and text.strip():
                    yield TranscriptionSegment(
                        text=text.strip(),
                        start=current_time,
                        end=current_time + buffer_duration,
                    )
            finally:
                if temp_path.exists():
                    temp_path.unlink()

    # =========================================================================
    # Вспомогательные методы
    # =========================================================================

    def get_model_info(self) -> Dict[str, Any]:
        """Получить информацию о модели."""
        return {
            "model_name": self.model_name,
            "device": self.device,
            "loaded": self._pipeline is not None and self._pipeline.engine.loaded,
            "hf_token_set": self.hf_token is not None,
            "cache_dir": str(self.cache_dir),
        }

    def preload(self) -> None:
        """Предзагрузка модели для ускорения первого запроса."""
        self.pipeline.prepare()
        logger.info("Модель предзагружена")


def create_transcriber(
    model_name: str = "v3_e2e_rnnt",
    device: str = "auto",
    hf_token: Optional[str] = None,
    **kwargs,
) -> GigaAMTranscriber:
    """
    Создание транскрибера с заданными параметрами.

    Это фабричная функция для удобного создания GigaAMTranscriber.

    Args:
        model_name: Имя модели
        device: Устройство
        hf_token: HuggingFace токен
        **kwargs: Дополнительные параметры

    Returns:
        Настроенный GigaAMTranscriber
    """
    return GigaAMTranscriber(
        model_name=model_name,
        device=device,
        hf_token=hf_token,
        **kwargs,
    )
