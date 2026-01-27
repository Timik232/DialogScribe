"""
Тесты для модуля data_models.
"""

import json
import pytest

from gigaam_transcriber import (
    TranscriptionResult,
    TranscriptionSegment,
    WordSegment,
    SpeakerSegment,
)


class TestWordSegment:
    """Тесты для WordSegment."""
    
    def test_creation(self):
        """Тест создания WordSegment."""
        word = WordSegment(word="привет", start=0.0, end=0.5)
        
        assert word.word == "привет"
        assert word.start == 0.0
        assert word.end == 0.5
        assert word.confidence is None
    
    def test_duration(self):
        """Тест вычисления длительности."""
        word = WordSegment(word="привет", start=1.0, end=2.5)
        
        assert word.duration == 1.5
    
    def test_to_dict(self):
        """Тест преобразования в словарь."""
        word = WordSegment(word="привет", start=0.0, end=0.5, confidence=0.95)
        d = word.to_dict()
        
        assert d["word"] == "привет"
        assert d["start"] == 0.0
        assert d["end"] == 0.5
        assert d["confidence"] == 0.95


class TestTranscriptionSegment:
    """Тесты для TranscriptionSegment."""
    
    def test_creation(self):
        """Тест создания TranscriptionSegment."""
        seg = TranscriptionSegment(
            text="Привет, как дела?",
            start=0.0,
            end=2.5,
            speaker="Спикер №1",
        )
        
        assert seg.text == "Привет, как дела?"
        assert seg.start == 0.0
        assert seg.end == 2.5
        assert seg.speaker == "Спикер №1"
    
    def test_duration(self):
        """Тест вычисления длительности."""
        seg = TranscriptionSegment(text="test", start=5.0, end=10.0)
        
        assert seg.duration == 5.0
    
    def test_to_dict(self):
        """Тест преобразования в словарь."""
        seg = TranscriptionSegment(
            text="Привет",
            start=0.0,
            end=1.0,
            speaker="Спикер №1",
            confidence=0.9,
        )
        d = seg.to_dict()
        
        assert d["text"] == "Привет"
        assert d["start"] == 0.0
        assert d["end"] == 1.0
        assert d["speaker"] == "Спикер №1"
        assert d["confidence"] == 0.9
    
    def test_from_dict(self):
        """Тест создания из словаря."""
        data = {
            "text": "Привет",
            "start": 0.0,
            "end": 1.0,
            "speaker": "Спикер №1",
        }
        seg = TranscriptionSegment.from_dict(data)
        
        assert seg.text == "Привет"
        assert seg.speaker == "Спикер №1"


class TestSpeakerSegment:
    """Тесты для SpeakerSegment."""
    
    def test_creation(self):
        """Тест создания SpeakerSegment."""
        seg = SpeakerSegment(start=0.0, end=5.0, speaker="SPEAKER_00")
        
        assert seg.start == 0.0
        assert seg.end == 5.0
        assert seg.speaker == "SPEAKER_00"
    
    def test_duration(self):
        """Тест вычисления длительности."""
        seg = SpeakerSegment(start=10.0, end=25.0, speaker="SPEAKER_01")
        
        assert seg.duration == 15.0


class TestTranscriptionResult:
    """Тесты для TranscriptionResult."""
    
    def test_creation(self, sample_transcription_result):
        """Тест создания TranscriptionResult."""
        result = sample_transcription_result
        
        assert len(result.segments) == 3
        assert result.duration == 5.5
        assert result.language == "ru"
        assert result.model_name == "v3_e2e_rnnt"
    
    def test_to_txt_with_timestamps(self, sample_transcription_result):
        """Тест форматирования в TXT с таймкодами."""
        txt = sample_transcription_result.to_txt(include_timestamps=True)
        
        assert "00:00:00" in txt
        assert "Спикер №1" in txt
        assert "Привет, как дела?" in txt
    
    def test_to_txt_without_timestamps(self, sample_transcription_result):
        """Тест форматирования в TXT без таймкодов."""
        txt = sample_transcription_result.to_txt(include_timestamps=False)
        
        assert "00:00" not in txt
        assert "Спикер №1:" in txt
    
    def test_to_json(self, sample_transcription_result):
        """Тест форматирования в JSON."""
        json_str = sample_transcription_result.to_json()
        data = json.loads(json_str)
        
        assert "metadata" in data
        assert "segments" in data
        assert "full_text" in data
        assert data["metadata"]["duration"] == 5.5
        assert len(data["segments"]) == 3
    
    def test_to_srt(self, sample_transcription_result):
        """Тест форматирования в SRT."""
        srt = sample_transcription_result.to_srt()
        
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:02,500" in srt
        assert "[Спикер №1]" in srt
    
    def test_to_vtt(self, sample_transcription_result):
        """Тест форматирования в WebVTT."""
        vtt = sample_transcription_result.to_vtt()
        
        assert vtt.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:02.500" in vtt
    
    def test_get_speakers(self, sample_transcription_result):
        """Тест получения списка спикеров."""
        speakers = sample_transcription_result.get_speakers()
        
        assert len(speakers) == 2
        assert "Спикер №1" in speakers
        assert "Спикер №2" in speakers
    
    def test_filter_by_speaker(self, sample_transcription_result):
        """Тест фильтрации по спикеру."""
        filtered = sample_transcription_result.filter_by_speaker("Спикер №2")
        
        assert len(filtered.segments) == 2
        assert all(s.speaker == "Спикер №2" for s in filtered.segments)
    
    def test_save(self, sample_transcription_result, temp_dir):
        """Тест сохранения в файл."""
        # TXT
        txt_path = temp_dir / "result.txt"
        sample_transcription_result.save(txt_path)
        assert txt_path.exists()
        assert "Привет" in txt_path.read_text()
        
        # JSON
        json_path = temp_dir / "result.json"
        sample_transcription_result.save(json_path, format="json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "segments" in data
        
        # SRT
        srt_path = temp_dir / "result.srt"
        sample_transcription_result.save(srt_path)
        assert srt_path.exists()
        assert "-->".encode() in srt_path.read_bytes()
