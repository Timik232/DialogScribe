"""
Тесты для модуля formatters.
"""

import json
import pytest

from gigaam_transcriber import (
    TranscriptionResult,
    TranscriptionSegment,
    OutputFormatter,
    format_output,
)
from gigaam_transcriber.formatters import TranscriptFormatter


class TestOutputFormatter:
    """Тесты для OutputFormatter."""
    
    @pytest.fixture
    def formatter(self):
        """Фикстура форматтера."""
        return OutputFormatter()
    
    @pytest.fixture
    def result(self):
        """Фикстура результата."""
        segments = [
            TranscriptionSegment(
                text="Привет, как дела?",
                start=0.0,
                end=2.5,
                speaker="Спикер №1",
            ),
            TranscriptionSegment(
                text="Отлично!",
                start=2.5,
                end=4.0,
                speaker="Спикер №2",
            ),
        ]
        return TranscriptionResult(
            text="Привет, как дела? Отлично!",
            segments=segments,
            duration=4.0,
            language="ru",
            model_name="v3_e2e_rnnt",
            processing_time=1.0,
            metadata={"source": "test.wav"},
        )
    
    def test_format_txt(self, formatter, result):
        """Тест форматирования в TXT."""
        txt = formatter.format(result, "txt")
        
        assert "Привет" in txt
        assert "Спикер №1" in txt
    
    def test_format_json(self, formatter, result):
        """Тест форматирования в JSON."""
        json_str = formatter.format(result, "json")
        data = json.loads(json_str)
        
        assert "segments" in data
        assert len(data["segments"]) == 2
    
    def test_format_srt(self, formatter, result):
        """Тест форматирования в SRT."""
        srt = formatter.format(result, "srt")
        
        assert "1\n" in srt
        assert "-->" in srt
    
    def test_format_vtt(self, formatter, result):
        """Тест форматирования в VTT."""
        vtt = formatter.format(result, "vtt")
        
        assert vtt.startswith("WEBVTT")
        assert "-->" in vtt
    
    def test_invalid_format(self, formatter, result):
        """Тест с неверным форматом."""
        with pytest.raises(ValueError):
            formatter.format(result, "invalid")


class TestTranscriptFormatter:
    """Тесты для TranscriptFormatter."""
    
    @pytest.fixture
    def segments(self):
        """Фикстура сегментов."""
        return [
            TranscriptionSegment(
                text="Привет",
                start=0.0,
                end=1.0,
                speaker="Спикер №1",
            ),
            TranscriptionSegment(
                text="Привет!",
                start=1.0,
                end=2.0,
                speaker="Спикер №2",
            ),
        ]
    
    def test_format_dialogue(self, segments):
        """Тест форматирования в виде диалога."""
        text = TranscriptFormatter.format_dialogue(segments)
        
        assert "═" in text  # Разделители
        assert "Спикер №1" in text
        assert "Спикер №2" in text
    
    def test_format_screenplay(self, segments):
        """Тест форматирования в стиле сценария."""
        text = TranscriptFormatter.format_screenplay(segments)
        
        assert "СПИКЕР №1" in text  # uppercase
        assert "Привет" in text
    
    def test_format_table(self, segments):
        """Тест форматирования в таблицу."""
        text = TranscriptFormatter.format_table(segments)
        
        assert "Start|End|Speaker|Text" in text
        assert "Спикер №1" in text


class TestFormatOutputFunction:
    """Тесты для функции format_output."""
    
    def test_basic(self, sample_transcription_result):
        """Базовый тест."""
        txt = format_output(sample_transcription_result, "txt")
        
        assert "Привет" in txt
    
    def test_json(self, sample_transcription_result):
        """Тест JSON."""
        json_str = format_output(sample_transcription_result, "json")
        data = json.loads(json_str)
        
        assert "segments" in data
