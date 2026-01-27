"""
Тесты для модуля segment_merger.
"""

import pytest

from gigaam_transcriber import (
    TranscriptionSegment,
    SpeakerSegment,
    SegmentMerger,
    MergeConfig,
    merge_segments,
)


class TestSegmentMerger:
    """Тесты для SegmentMerger."""
    
    def test_merge_same_speaker_segments(self):
        """Тест объединения сегментов одного спикера."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="как дела", start=1.0, end=2.0, speaker="Спикер №1"),
            TranscriptionSegment(text="Отлично", start=2.0, end=3.0, speaker="Спикер №2"),
        ]
        
        merger = SegmentMerger()
        merged = merger.merge_same_speaker_segments(segments)
        
        assert len(merged) == 2
        assert merged[0].text == "Привет как дела"
        assert merged[0].speaker == "Спикер №1"
        assert merged[0].start == 0.0
        assert merged[0].end == 2.0
        assert merged[1].speaker == "Спикер №2"
    
    def test_merge_with_gap(self):
        """Тест объединения с учётом gap."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="как дела", start=1.5, end=2.5, speaker="Спикер №1"),  # gap = 0.5
            TranscriptionSegment(text="хорошо", start=5.0, end=6.0, speaker="Спикер №1"),  # gap = 2.5
        ]
        
        # С max_gap=1.0 первые два должны объединиться
        merger = SegmentMerger(MergeConfig(max_gap=1.0))
        merged = merger.merge_same_speaker_segments(segments)
        
        assert len(merged) == 2
        assert merged[0].text == "Привет как дела"
        assert merged[1].text == "хорошо"
    
    def test_no_merge_different_speakers(self):
        """Тест: не объединяем разных спикеров."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="Привет", start=1.0, end=2.0, speaker="Спикер №2"),
        ]
        
        merger = SegmentMerger()
        merged = merger.merge_same_speaker_segments(segments)
        
        assert len(merged) == 2
    
    def test_merge_short_segments(self):
        """Тест объединения коротких сегментов."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=0.2, speaker="Спикер №1"),  # короткий
            TranscriptionSegment(text="как дела сегодня", start=0.2, end=2.0, speaker="Спикер №1"),
        ]
        
        merger = SegmentMerger(MergeConfig(min_segment_duration=0.5))
        merged = merger.merge_short_segments(segments)
        
        # Короткий сегмент должен объединиться с соседним
        assert len(merged) == 1
        assert "Привет" in merged[0].text
    
    def test_merge_speaker_segments(self):
        """Тест объединения сегментов диаризации."""
        segments = [
            SpeakerSegment(start=0.0, end=1.0, speaker="SPEAKER_00"),
            SpeakerSegment(start=1.0, end=2.0, speaker="SPEAKER_00"),
            SpeakerSegment(start=2.5, end=3.5, speaker="SPEAKER_01"),
        ]
        
        merger = SegmentMerger()
        merged = merger.merge_speaker_segments(segments)
        
        assert len(merged) == 2
        assert merged[0].start == 0.0
        assert merged[0].end == 2.0
    
    def test_empty_segments(self):
        """Тест с пустым списком сегментов."""
        merger = SegmentMerger()
        
        assert merger.merge_same_speaker_segments([]) == []
        assert merger.merge_short_segments([]) == []
        assert merger.merge_speaker_segments([]) == []
    
    def test_single_segment(self):
        """Тест с одним сегментом."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
        ]
        
        merger = SegmentMerger()
        merged = merger.merge_same_speaker_segments(segments)
        
        assert len(merged) == 1
        assert merged[0].text == "Привет"


class TestMergeSegmentsFunction:
    """Тесты для функции merge_segments."""
    
    def test_basic_merge(self):
        """Базовый тест функции merge_segments."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="мир", start=1.0, end=2.0, speaker="Спикер №1"),
        ]
        
        merged = merge_segments(segments, max_gap=1.0)
        
        assert len(merged) == 1
        assert merged[0].text == "Привет мир"
    
    def test_no_merge_flag(self):
        """Тест с отключённым объединением."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="мир", start=1.0, end=2.0, speaker="Спикер №1"),
        ]
        
        result = merge_segments(segments, merge_same_speaker=False)
        
        assert len(result) == 2
