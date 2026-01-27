"""
Модуль сшивки сегментов для GigaAM Transcriber.

Обеспечивает:
- Объединение смежных сегментов одного спикера
- Слияние коротких сегментов
- Выравнивание границ сегментов
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from .data_models import SpeakerSegment, TranscriptionSegment

logger = logging.getLogger(__name__)


@dataclass
class MergeConfig:
    """Конфигурация сшивки сегментов."""
    
    # Максимальный gap для объединения сегментов одного спикера (секунды)
    max_gap: float = 1.0
    
    # Минимальная длительность сегмента (секунды)
    min_segment_duration: float = 0.3
    
    # Максимальная длительность объединённого сегмента (секунды)
    max_merged_duration: float = 60.0
    
    # Объединять ли сегменты одного спикера
    merge_same_speaker: bool = True
    
    # Объединять ли очень короткие сегменты с соседними
    merge_short_segments: bool = True


class SegmentMerger:
    """Класс для сшивки и оптимизации сегментов."""
    
    def __init__(self, config: Optional[MergeConfig] = None):
        """
        Инициализация.
        
        Args:
            config: Конфигурация сшивки
        """
        self.config = config or MergeConfig()
    
    def merge_same_speaker_segments(
        self,
        segments: List[TranscriptionSegment],
        max_gap: Optional[float] = None,
    ) -> List[TranscriptionSegment]:
        """
        Объединение последовательных сегментов одного спикера.
        
        Пример:
        До:
            [00:00-00:05] Спикер 1: Привет
            [00:05-00:08] Спикер 1: как дела
            [00:08-00:12] Спикер 2: Отлично
        
        После:
            [00:00-00:08] Спикер 1: Привет как дела
            [00:08-00:12] Спикер 2: Отлично
        
        Args:
            segments: Список сегментов для объединения
            max_gap: Максимальный gap для объединения (секунды)
            
        Returns:
            Список объединённых сегментов
        """
        if not segments:
            return []
        
        max_gap = max_gap if max_gap is not None else self.config.max_gap
        
        merged = []
        current = None
        
        for seg in segments:
            if current is None:
                # Первый сегмент
                current = TranscriptionSegment(
                    text=seg.text,
                    start=seg.start,
                    end=seg.end,
                    speaker=seg.speaker,
                    confidence=seg.confidence,
                    words=list(seg.words) if seg.words else None,
                )
            elif self._should_merge(current, seg, max_gap):
                # Объединяем с текущим
                current = self._merge_two_segments(current, seg)
            else:
                # Сохраняем текущий и начинаем новый
                merged.append(current)
                current = TranscriptionSegment(
                    text=seg.text,
                    start=seg.start,
                    end=seg.end,
                    speaker=seg.speaker,
                    confidence=seg.confidence,
                    words=list(seg.words) if seg.words else None,
                )
        
        # Добавляем последний сегмент
        if current:
            merged.append(current)
        
        return merged
    
    def _should_merge(
        self,
        current: TranscriptionSegment,
        next_seg: TranscriptionSegment,
        max_gap: float,
    ) -> bool:
        """Определить, нужно ли объединять сегменты."""
        # Разные спикеры - не объединяем
        if current.speaker != next_seg.speaker:
            return False
        
        # Gap слишком большой - не объединяем
        gap = next_seg.start - current.end
        if gap > max_gap:
            return False
        
        # Проверка на максимальную длительность
        merged_duration = next_seg.end - current.start
        if merged_duration > self.config.max_merged_duration:
            return False
        
        return True
    
    def _merge_two_segments(
        self,
        seg1: TranscriptionSegment,
        seg2: TranscriptionSegment,
    ) -> TranscriptionSegment:
        """Объединить два сегмента в один."""
        # Объединение текста
        text = seg1.text.rstrip() + " " + seg2.text.lstrip()
        
        # Объединение слов
        words = None
        if seg1.words and seg2.words:
            words = list(seg1.words) + list(seg2.words)
        elif seg1.words:
            words = list(seg1.words)
        elif seg2.words:
            words = list(seg2.words)
        
        # Усреднение confidence
        confidence = None
        if seg1.confidence is not None and seg2.confidence is not None:
            confidence = (seg1.confidence + seg2.confidence) / 2
        elif seg1.confidence is not None:
            confidence = seg1.confidence
        elif seg2.confidence is not None:
            confidence = seg2.confidence
        
        return TranscriptionSegment(
            text=text,
            start=seg1.start,
            end=seg2.end,
            speaker=seg1.speaker,
            confidence=confidence,
            words=words,
        )
    
    def merge_short_segments(
        self,
        segments: List[TranscriptionSegment],
        min_duration: Optional[float] = None,
    ) -> List[TranscriptionSegment]:
        """
        Объединение очень коротких сегментов с соседними.
        
        Args:
            segments: Список сегментов
            min_duration: Минимальная длительность сегмента
            
        Returns:
            Список с объединёнными короткими сегментами
        """
        if not segments or len(segments) < 2:
            return segments
        
        min_duration = min_duration or self.config.min_segment_duration
        
        result = []
        i = 0
        
        while i < len(segments):
            current = segments[i]
            
            # Если сегмент достаточно длинный или последний
            if current.duration >= min_duration or i == len(segments) - 1:
                result.append(current)
                i += 1
                continue
            
            # Короткий сегмент - объединяем с ближайшим
            next_seg = segments[i + 1] if i + 1 < len(segments) else None
            prev_seg = result[-1] if result else None
            
            # Определяем, с кем объединять
            if prev_seg is None:
                # Объединяем со следующим
                if next_seg:
                    merged = self._merge_two_segments(current, next_seg)
                    segments[i + 1] = merged
                    i += 1
                else:
                    result.append(current)
                    i += 1
            elif next_seg is None:
                # Объединяем с предыдущим
                result[-1] = self._merge_two_segments(prev_seg, current)
                i += 1
            else:
                # Выбираем ближайшего соседа с тем же спикером
                if current.speaker == prev_seg.speaker:
                    result[-1] = self._merge_two_segments(prev_seg, current)
                elif current.speaker == next_seg.speaker:
                    merged = self._merge_two_segments(current, next_seg)
                    segments[i + 1] = merged
                else:
                    # Объединяем с ближайшим по времени
                    gap_to_prev = current.start - prev_seg.end
                    gap_to_next = next_seg.start - current.end
                    
                    if gap_to_prev <= gap_to_next:
                        result[-1] = self._merge_two_segments(prev_seg, current)
                    else:
                        merged = self._merge_two_segments(current, next_seg)
                        segments[i + 1] = merged
                i += 1
        
        return result
    
    def merge_speaker_segments(
        self,
        segments: List[SpeakerSegment],
        max_gap: Optional[float] = None,
    ) -> List[SpeakerSegment]:
        """
        Объединение смежных сегментов диаризации одного спикера.
        
        Args:
            segments: Сегменты диаризации
            max_gap: Максимальный gap для объединения
            
        Returns:
            Объединённые сегменты
        """
        if not segments:
            return []
        
        max_gap = max_gap if max_gap is not None else self.config.max_gap
        
        merged = []
        current = None
        
        for seg in sorted(segments, key=lambda s: s.start):
            if current is None:
                current = SpeakerSegment(
                    start=seg.start,
                    end=seg.end,
                    speaker=seg.speaker
                )
            elif (current.speaker == seg.speaker and 
                  seg.start - current.end <= max_gap):
                # Объединяем
                current.end = seg.end
            else:
                merged.append(current)
                current = SpeakerSegment(
                    start=seg.start,
                    end=seg.end,
                    speaker=seg.speaker
                )
        
        if current:
            merged.append(current)
        
        return merged
    
    def align_segment_boundaries(
        self,
        transcription_segments: List[TranscriptionSegment],
        speaker_segments: List[SpeakerSegment],
    ) -> List[TranscriptionSegment]:
        """
        Выравнивание границ сегментов транскрипции по границам диаризации.
        
        Это помогает убрать артефакты на стыках реплик разных спикеров.
        
        Args:
            transcription_segments: Сегменты транскрипции
            speaker_segments: Сегменты диаризации
            
        Returns:
            Сегменты с выровненными границами
        """
        if not transcription_segments or not speaker_segments:
            return transcription_segments
        
        # Создаём список всех границ спикеров
        speaker_boundaries = set()
        for seg in speaker_segments:
            speaker_boundaries.add(seg.start)
            speaker_boundaries.add(seg.end)
        speaker_boundaries = sorted(speaker_boundaries)
        
        # Выравниваем границы транскрипции
        for seg in transcription_segments:
            # Выравниваем start
            seg.start = self._find_nearest_boundary(
                seg.start, speaker_boundaries, tolerance=0.3
            )
            # Выравниваем end
            seg.end = self._find_nearest_boundary(
                seg.end, speaker_boundaries, tolerance=0.3
            )
        
        return transcription_segments
    
    def _find_nearest_boundary(
        self,
        time: float,
        boundaries: List[float],
        tolerance: float,
    ) -> float:
        """Найти ближайшую границу в пределах tolerance."""
        for boundary in boundaries:
            if abs(boundary - time) <= tolerance:
                return boundary
        return time
    
    def process(
        self,
        segments: List[TranscriptionSegment],
        speaker_segments: Optional[List[SpeakerSegment]] = None,
    ) -> List[TranscriptionSegment]:
        """
        Полная обработка сегментов: сшивка и оптимизация.
        
        Args:
            segments: Сегменты транскрипции
            speaker_segments: Сегменты диаризации (опционально)
            
        Returns:
            Оптимизированные сегменты
        """
        if not segments:
            return []
        
        result = segments
        
        # Выравнивание по границам диаризации
        if speaker_segments:
            result = self.align_segment_boundaries(result, speaker_segments)
        
        # Объединение сегментов одного спикера
        if self.config.merge_same_speaker:
            result = self.merge_same_speaker_segments(result)
        
        # Объединение коротких сегментов
        if self.config.merge_short_segments:
            result = self.merge_short_segments(result)
        
        return result


def merge_segments(
    segments: List[TranscriptionSegment],
    max_gap: float = 1.0,
    merge_same_speaker: bool = True,
) -> List[TranscriptionSegment]:
    """
    Удобная функция для быстрой сшивки сегментов.
    
    Args:
        segments: Сегменты для объединения
        max_gap: Максимальный gap между сегментами
        merge_same_speaker: Объединять ли сегменты одного спикера
        
    Returns:
        Объединённые сегменты
    """
    config = MergeConfig(
        max_gap=max_gap,
        merge_same_speaker=merge_same_speaker,
    )
    merger = SegmentMerger(config)
    
    if merge_same_speaker:
        return merger.merge_same_speaker_segments(segments, max_gap)
    return segments
