"""
Тесты для модуля audio_processor.
"""

import pytest
from pathlib import Path

from gigaam_transcriber import (
    AudioProcessor,
    UnsupportedFormatError,
    FFmpegNotFoundError,
)


class TestAudioProcessor:
    """Тесты для AudioProcessor."""
    
    def test_is_audio_file(self):
        """Тест определения аудио файлов."""
        assert AudioProcessor.is_audio_file("test.wav")
        assert AudioProcessor.is_audio_file("test.mp3")
        assert AudioProcessor.is_audio_file("test.flac")
        assert AudioProcessor.is_audio_file(Path("test.ogg"))
        
        assert not AudioProcessor.is_audio_file("test.mp4")
        assert not AudioProcessor.is_audio_file("test.txt")
    
    def test_is_video_file(self):
        """Тест определения видео файлов."""
        assert AudioProcessor.is_video_file("test.mp4")
        assert AudioProcessor.is_video_file("test.mkv")
        assert AudioProcessor.is_video_file("test.avi")
        assert AudioProcessor.is_video_file(Path("test.webm"))
        
        assert not AudioProcessor.is_video_file("test.wav")
        assert not AudioProcessor.is_video_file("test.txt")
    
    def test_is_supported_file(self):
        """Тест определения поддерживаемых файлов."""
        # Аудио
        assert AudioProcessor.is_supported_file("test.wav")
        assert AudioProcessor.is_supported_file("test.mp3")
        
        # Видео
        assert AudioProcessor.is_supported_file("test.mp4")
        assert AudioProcessor.is_supported_file("test.mkv")
        
        # Не поддерживается
        assert not AudioProcessor.is_supported_file("test.txt")
        assert not AudioProcessor.is_supported_file("test.pdf")
    
    def test_initialization(self):
        """Тест инициализации процессора."""
        try:
            processor = AudioProcessor()
            assert processor.ffmpeg_path is not None
        except FFmpegNotFoundError:
            pytest.skip("FFmpeg не установлен")
    
    def test_sample_rate_constant(self):
        """Тест константы SAMPLE_RATE."""
        assert AudioProcessor.SAMPLE_RATE == 16000
    
    def test_channels_constant(self):
        """Тест константы CHANNELS."""
        assert AudioProcessor.CHANNELS == 1


class TestAudioProcessorFormats:
    """Тесты форматов AudioProcessor."""
    
    def test_audio_formats(self):
        """Тест списка аудио форматов."""
        formats = AudioProcessor.AUDIO_FORMATS
        
        assert '.wav' in formats
        assert '.mp3' in formats
        assert '.flac' in formats
        assert '.ogg' in formats
        assert '.m4a' in formats
    
    def test_video_formats(self):
        """Тест списка видео форматов."""
        formats = AudioProcessor.VIDEO_FORMATS
        
        assert '.mp4' in formats
        assert '.mkv' in formats
        assert '.avi' in formats
        assert '.mov' in formats
        assert '.webm' in formats
