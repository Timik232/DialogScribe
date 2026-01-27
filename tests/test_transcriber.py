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
        assert transcriber._model is None  # Lazy loading
    
    def test_custom_model_name(self):
        """Тест с кастомным именем модели."""
        transcriber = GigaAMTranscriber(model_name="v3_e2e_ctc")
        
        assert transcriber.model_name == "v3_e2e_ctc"
    
    def test_device_resolution_auto(self):
        """Тест автоопределения устройства."""
        transcriber = GigaAMTranscriber(device="auto")
        
        # Должно быть либо cuda, либо cpu
        assert transcriber.device in ["cuda", "cpu"]
    
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
        transcriber._model = Mock()  # Симуляция загруженной модели
        
        transcriber.cleanup()
        
        assert transcriber._model is None


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
        assert transcriber._model is not None
    
    def test_preload(self):
        """Тест предзагрузки модели."""
        transcriber = GigaAMTranscriber(device="cpu")
        
        assert transcriber._model is None
        transcriber.preload()
        assert transcriber._model is not None
        
        transcriber.cleanup()


class TestGigaAMTranscriberMocked:
    """Тесты с моками (без реальной модели)."""
    
    @pytest.fixture
    def mock_transcriber(self):
        """Фикстура транскрибера с замоканной моделью."""
        transcriber = GigaAMTranscriber()
        
        # Мокаем модель
        mock_model = MagicMock()
        mock_model.transcribe.return_value = "Тестовая транскрипция"
        mock_model.transcribe_longform.return_value = [
            {"transcription": "Первый сегмент", "boundaries": (0.0, 5.0)},
            {"transcription": "Второй сегмент", "boundaries": (5.0, 10.0)},
        ]
        
        transcriber._model = mock_model
        
        # Мокаем audio_processor
        mock_processor = MagicMock()
        mock_processor.is_audio_file.return_value = True
        mock_processor.is_video_file.return_value = False
        mock_processor.is_supported_file.return_value = True
        mock_processor.get_duration.return_value = 5.0
        mock_processor.get_media_info.return_value = {
            "duration": 5.0,
            "sample_rate": 16000,
            "channels": 1,
        }
        
        transcriber._audio_processor = mock_processor
        
        return transcriber
    
    def test_transcribe_short_mocked(self, mock_transcriber, temp_dir):
        """Тест транскрипции короткого аудио (мок)."""
        # Создаём фейковый файл
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")
        
        mock_transcriber._audio_processor.get_duration.return_value = 10.0
        
        segments = mock_transcriber._transcribe_short(audio_file)
        
        assert len(segments) == 1
        assert segments[0].text == "Тестовая транскрипция"
    
    def test_transcribe_long_mocked(self, mock_transcriber, temp_dir):
        """Тест транскрипции длинного аудио (мок)."""
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")
        
        segments = mock_transcriber._transcribe_long(audio_file)
        
        assert len(segments) == 2
        assert segments[0].text == "Первый сегмент"
        assert segments[1].text == "Второй сегмент"
