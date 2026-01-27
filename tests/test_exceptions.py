"""
Тесты для модуля exceptions.
"""

import pytest

from gigaam_transcriber import (
    TranscriberError,
    AudioTooShortError,
    AudioTooLongError,
    UnsupportedFormatError,
    DiarizationError,
    HFTokenMissingError,
    ModelLoadError,
    AudioProcessingError,
    FFmpegNotFoundError,
    EmptyAudioError,
    EmptyFileError,
)


class TestTranscriberError:
    """Тесты базового исключения."""
    
    def test_basic(self):
        """Базовый тест."""
        error = TranscriberError("Тестовая ошибка")
        assert str(error) == "Тестовая ошибка"


class TestAudioTooShortError:
    """Тесты AudioTooShortError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = AudioTooShortError(duration=0.05, min_duration=0.1)
        
        assert "0.05" in str(error)
        assert "0.1" in str(error)
        assert error.duration == 0.05
        assert error.min_duration == 0.1


class TestAudioTooLongError:
    """Тесты AudioTooLongError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = AudioTooLongError(duration=30.0, max_duration=25.0)
        
        assert "30.0" in str(error)
        assert "25.0" in str(error)
        assert "transcribe_longform" in str(error)


class TestUnsupportedFormatError:
    """Тесты UnsupportedFormatError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = UnsupportedFormatError(".xyz")
        
        assert ".xyz" in str(error)
        assert ".wav" in str(error)  # Должны быть перечислены поддерживаемые форматы
    
    def test_supported_formats(self):
        """Тест списков поддерживаемых форматов."""
        assert '.wav' in UnsupportedFormatError.SUPPORTED_AUDIO
        assert '.mp4' in UnsupportedFormatError.SUPPORTED_VIDEO


class TestDiarizationError:
    """Тесты DiarizationError."""
    
    def test_with_cause(self):
        """Тест с причиной ошибки."""
        cause = ValueError("Тест")
        error = DiarizationError("Ошибка диаризации", cause=cause)
        
        assert error.cause is cause
        assert "Ошибка диаризации" in str(error)


class TestHFTokenMissingError:
    """Тесты HFTokenMissingError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = HFTokenMissingError()
        
        assert "HF_TOKEN" in str(error)
        assert "HuggingFace" in str(error)


class TestModelLoadError:
    """Тесты ModelLoadError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = ModelLoadError("v3_e2e_rnnt")
        
        assert "v3_e2e_rnnt" in str(error)
        assert error.model_name == "v3_e2e_rnnt"
    
    def test_with_cause(self):
        """Тест с причиной."""
        cause = RuntimeError("Недостаточно памяти")
        error = ModelLoadError("v3_e2e_rnnt", cause=cause)
        
        assert "Недостаточно памяти" in str(error)
        assert error.cause is cause


class TestAudioProcessingError:
    """Тесты AudioProcessingError."""
    
    def test_with_file_path(self):
        """Тест с путём к файлу."""
        error = AudioProcessingError("Ошибка конвертации", file_path="/path/to/file.wav")
        
        assert "/path/to/file.wav" in str(error)
        assert error.file_path == "/path/to/file.wav"


class TestFFmpegNotFoundError:
    """Тесты FFmpegNotFoundError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = FFmpegNotFoundError()
        
        assert "FFmpeg" in str(error)
        assert "ffmpeg.org" in str(error)


class TestEmptyAudioError:
    """Тесты EmptyAudioError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = EmptyAudioError("/path/to/empty.wav")
        
        assert "речи" in str(error).lower() or "audio" in str(error).lower()


class TestEmptyFileError:
    """Тесты EmptyFileError."""
    
    def test_message(self):
        """Тест сообщения об ошибке."""
        error = EmptyFileError("/path/to/empty.wav")
        
        assert "/path/to/empty.wav" in str(error)
