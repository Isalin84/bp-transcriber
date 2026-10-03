"""
Тесты для модуля transcriber (основной класс).

Примечание: тесты, требующие загрузки модели GigaAM, помечены как @pytest.mark.requires_model
и по умолчанию пропускаются. Для запуска используйте: pytest -m requires_model
"""

import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from gigaam_transcriber import (
    GigaAMTranscriber,
    create_transcriber,
    TranscriptionResult,
    TranscriptionSegment,
    ModelLoadError,
    UnsupportedFormatError,
)


class TestGigaAMTranscriberInit:
    """Тесты инициализации GigaAMTranscriber."""
    
    def test_default_init(self):
        """Тест инициализации с параметрами по умолчанию."""
        # Не загружаем модель при инициализации (lazy loading)
        transcriber = GigaAMTranscriber()
        
        assert transcriber.model_name == "v3_e2e_rnnt"
        assert transcriber._pipeline is None  # Lazy loading
        assert transcriber.get_model_info()["loaded"] is False
    
    def test_custom_model_name(self):
        """Тест с кастомным именем модели."""
        transcriber = GigaAMTranscriber(model_name="v3_e2e_ctc")
        
        assert transcriber.model_name == "v3_e2e_ctc"
    
    def test_device_resolution_auto(self):
        """Тест автоопределения устройства."""
        transcriber = GigaAMTranscriber(device="auto")
        
        # cuda > mps > cpu
        assert transcriber.device in ["cuda", "mps", "cpu"]
    
    def test_device_explicit(self):
        """Тест с явным указанием устройства."""
        transcriber = GigaAMTranscriber(device="cpu")
        
        assert transcriber.device == "cpu"
    
    def test_hf_token_from_env(self):
        """Тест получения HF_TOKEN из переменной окружения."""
        with patch.dict(os.environ, {"HF_TOKEN": "test_token"}):
            transcriber = GigaAMTranscriber()
            
            assert transcriber.hf_token == "test_token"
    
    def test_hf_token_explicit(self):
        """Тест с явным указанием токена."""
        transcriber = GigaAMTranscriber(hf_token="explicit_token")
        
        assert transcriber.hf_token == "explicit_token"
    
    def test_cache_dir_default(self):
        """Тест директории кэша по умолчанию."""
        transcriber = GigaAMTranscriber()
        
        assert transcriber.cache_dir.exists()
        assert "gigaam_transcriber" in str(transcriber.cache_dir)
    
    def test_cache_dir_custom(self, temp_dir):
        """Тест с кастомной директорией кэша."""
        cache_dir = temp_dir / "custom_cache"
        transcriber = GigaAMTranscriber(cache_dir=cache_dir)
        
        assert transcriber.cache_dir == cache_dir
        assert cache_dir.exists()


class TestGigaAMTranscriberContextManager:
    """Тесты контекстного менеджера."""
    
    def test_context_manager_enter(self):
        """Тест входа в контекст."""
        with GigaAMTranscriber() as transcriber:
            assert isinstance(transcriber, GigaAMTranscriber)
    
    def test_context_manager_cleanup(self):
        """Тест очистки при выходе из контекста."""
        transcriber = GigaAMTranscriber()
        transcriber._pipeline = Mock()  # Симуляция конвейера с моделями
        
        transcriber.cleanup()
        
        transcriber._pipeline.close.assert_called_once()


class TestGigaAMTranscriberGetModelInfo:
    """Тесты метода get_model_info."""
    
    def test_get_model_info(self):
        """Тест получения информации о модели."""
        transcriber = GigaAMTranscriber()
        info = transcriber.get_model_info()
        
        assert "model_name" in info
        assert "device" in info
        assert "loaded" in info
        assert info["model_name"] == "v3_e2e_rnnt"
        assert info["loaded"] is False  # Модель ещё не загружена


class TestGigaAMTranscriberValidation:
    """Тесты валидации входных данных."""
    
    def test_validate_unsupported_format(self, temp_dir):
        """Тест с неподдерживаемым форматом."""
        transcriber = GigaAMTranscriber()
        
        # Создаём файл с неподдерживаемым расширением
        bad_file = temp_dir / "test.xyz"
        bad_file.write_text("test")
        
        with pytest.raises(UnsupportedFormatError):
            transcriber._validate_input(bad_file)
    
    def test_validate_nonexistent_file(self):
        """Тест с несуществующим файлом."""
        transcriber = GigaAMTranscriber()
        
        with pytest.raises(FileNotFoundError):
            transcriber._validate_input(Path("/nonexistent/file.wav"))


class TestCreateTranscriberFunction:
    """Тесты для функции create_transcriber."""
    
    def test_create_default(self):
        """Тест создания с параметрами по умолчанию."""
        transcriber = create_transcriber()
        
        assert isinstance(transcriber, GigaAMTranscriber)
        assert transcriber.model_name == "v3_e2e_rnnt"
    
    def test_create_with_params(self):
        """Тест создания с параметрами."""
        transcriber = create_transcriber(
            model_name="v3_e2e_ctc",
            device="cpu",
            hf_token="test",
        )
        
        assert transcriber.model_name == "v3_e2e_ctc"
        assert transcriber.device == "cpu"
        assert transcriber.hf_token == "test"


@pytest.mark.requires_model
class TestGigaAMTranscriberWithModel:
    """
    Тесты, требующие загрузки модели GigaAM.
    
    Запуск: pytest -m requires_model
    """
    
    @pytest.fixture(scope="class")
    def transcriber(self):
        """Фикстура транскрибера с загруженной моделью."""
        t = GigaAMTranscriber(device="cpu")
        t.preload()
        yield t
        t.cleanup()
    
    def test_model_loaded(self, transcriber):
        """Тест загрузки модели."""
        assert transcriber.pipeline.engine.loaded
    
    def test_preload(self):
        """Тест предзагрузки модели."""
        transcriber = GigaAMTranscriber(device="cpu")
        
        assert not transcriber.get_model_info()["loaded"]
        transcriber.preload()
        assert transcriber.get_model_info()["loaded"]
        
        transcriber.cleanup()
        assert not transcriber.get_model_info()["loaded"]


class TestGigaAMTranscriberMocked:
    """transcribe() делегирует TranscriptionPipeline (без реальной модели)."""
    
    @pytest.fixture
    def mocked(self, temp_dir):
        transcriber = GigaAMTranscriber()
        pipeline = MagicMock()
        pipeline.run.return_value = TranscriptionResult(
            text="Первый сегмент Второй сегмент",
            segments=[
                TranscriptionSegment(text="Первый сегмент", start=0.0, end=5.0, speaker="Спикер 1"),
                TranscriptionSegment(text="Второй сегмент", start=5.0, end=10.0, speaker="Спикер 2"),
            ],
            duration=10.0,
            language="ru",
            model_name="v3_e2e_rnnt",
            processing_time=1.0,
            metadata={"source": "test.wav"},
        )
        transcriber._pipeline = pipeline
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")
        return transcriber, pipeline, audio_file
    
    def test_options_mapping(self, mocked):
        """Параметры CLI переходят в PipelineOptions."""
        transcriber, pipeline, audio_file = mocked
        
        result = transcriber.transcribe(
            audio_file, diarization="pyannote", num_speakers=2, min_segment_gap=0.7
        )
        
        path, options = pipeline.run.call_args.args
        assert path == audio_file
        assert options.diarization == "pyannote"
        assert options.num_speakers == 2
        assert options.max_gap == 0.7
        assert options.soft_max == 20.0
        assert [s.text for s in result.segments] == ["Первый сегмент", "Второй сегмент"]
    
    def test_no_merge_splits_sentences(self, mocked):
        """merge_same_speaker=False -> сегмент на каждое предложение."""
        transcriber, pipeline, audio_file = mocked
        
        transcriber.transcribe(audio_file, merge_same_speaker=False)
        
        options = pipeline.run.call_args.args[1]
        assert options.soft_max == 0.0
        assert options.diarization == "none"
    
    def test_output_saved(self, mocked, temp_dir):
        """Результат сохраняется в output_path."""
        transcriber, _, audio_file = mocked
        out = temp_dir / "out.srt"
        
        transcriber.transcribe(audio_file, output_path=out, output_format="srt")
        
        assert "Первый сегмент" in out.read_text(encoding="utf-8")
