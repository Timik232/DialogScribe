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


class TestMergeShortSpeakerSegments:
    """Тесты для merge_short_speaker_segments."""

    def test_merge_short_same_speaker(self):
        """Тест объединения коротких сегментов одного спикера."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=0.3, speaker='A'),
            SpeakerSegment(start=0.3, end=0.6, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].end == 0.6
        assert result[0].speaker == 'A'

    def test_merge_short_chain(self):
        """Тест цепочного объединения нескольких коротких сегментов одного спикера."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=0.3, speaker='A'),
            SpeakerSegment(start=0.3, end=0.6, speaker='A'),
            SpeakerSegment(start=0.6, end=0.9, speaker='A'),
            SpeakerSegment(start=0.9, end=5.0, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].end == 5.0
        assert result[0].speaker == 'A'

    def test_no_cross_speaker_merge(self):
        """Тест: короткий сегмент между разными спикерами не объединяется."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=5.0, speaker='A'),
            SpeakerSegment(start=5.0, end=5.5, speaker='B'),
            SpeakerSegment(start=5.5, end=10.0, speaker='C'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 3
        assert result[0].speaker == 'A'
        assert result[0].start == 0.0
        assert result[0].end == 5.0
        assert result[1].speaker == 'B'
        assert result[1].start == 5.0
        assert result[1].end == 5.5
        assert result[2].speaker == 'C'
        assert result[2].start == 5.5
        assert result[2].end == 10.0

    def test_long_segments_unchanged(self):
        """Тест: длинные сегменты (выше порога) не объединяются."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=5.0, speaker='A'),
            SpeakerSegment(start=5.0, end=10.0, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 2
        assert result[0].start == 0.0
        assert result[0].end == 5.0
        assert result[1].start == 5.0
        assert result[1].end == 10.0

    def test_empty_list(self):
        """Тест: пустой список возвращает пустой список."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        result = merger.merge_short_speaker_segments([])

        assert result == []

    def test_single_segment(self):
        """Тест: один сегмент возвращается без изменений."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=0.3, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].end == 0.3
        assert result[0].speaker == 'A'

    def test_mixed_long_and_short(self):
        """Тест: смесь длинных и коротких сегментов одного спикера."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=0.5, speaker='A'),
            SpeakerSegment(start=0.5, end=5.0, speaker='A'),
            SpeakerSegment(start=5.0, end=5.3, speaker='A'),
            SpeakerSegment(start=5.3, end=10.0, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 2
        assert result[0].start == 0.0
        assert result[0].end == 5.3
        assert result[0].speaker == 'A'
        assert result[1].start == 5.3
        assert result[1].end == 10.0
        assert result[1].speaker == 'A'

    def test_max_merged_duration(self):
        """Тест: объединение ограничено max_merged_duration."""
        merger = SegmentMerger(
            MergeConfig(min_presplit_duration=1.0, max_merged_duration=5.0)
        )
        segments = [
            SpeakerSegment(start=0.0, end=3.0, speaker='A'),
            SpeakerSegment(start=3.0, end=3.5, speaker='A'),
            SpeakerSegment(start=3.5, end=7.0, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        # [0-3] длинный, [3-3.5] короткий → поглощается (0-3.5 < 5)
        # [3.5-7] длинный, prev в result = [0-3.5], merged_dur = 7-0 = 7 > 5 → не объединяется
        assert len(result) == 2
        assert result[0].start == 0.0
        assert result[0].end == 3.5
        assert result[1].start == 3.5
        assert result[1].end == 7.0

    def test_short_first_segment(self):
        """Тест: первый короткий сегмент объединяется со следующим того же спикера."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=0.3, speaker='A'),
            SpeakerSegment(start=0.3, end=5.0, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].end == 5.0
        assert result[0].speaker == 'A'

    def test_short_last_segment(self):
        """Тест: последний короткий сегмент объединяется с предыдущим того же спикера."""
        merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
        segments = [
            SpeakerSegment(start=0.0, end=5.0, speaker='A'),
            SpeakerSegment(start=5.0, end=5.3, speaker='A'),
        ]
        result = merger.merge_short_speaker_segments(segments)

        assert len(result) == 1
        assert result[0].start == 0.0
        assert result[0].end == 5.3
        assert result[0].speaker == 'A'
