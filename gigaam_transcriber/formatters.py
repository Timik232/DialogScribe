"""
Форматтеры вывода для GigaAM Transcriber.

Дополнительные форматы и утилиты для работы с результатами транскрипции.
"""

import json
from pathlib import Path
from typing import List, Optional

from .data_models import (
    OutputFormat,
    TranscriptionResult,
    TranscriptionSegment,
    _format_time_srt,
    _format_time_txt,
    _format_time_vtt,
)


class OutputFormatter:
    """Форматтер для вывода результатов транскрипции."""
    
    def __init__(
        self,
        include_timestamps: bool = True,
        include_speakers: bool = True,
        speaker_format: str = "{speaker}:",
        timestamp_format: str = "[{start} - {end}]",
    ):
        """
        Инициализация форматтера.
        
        Args:
            include_timestamps: Включать временные метки
            include_speakers: Включать имена спикеров
            speaker_format: Формат отображения спикера
            timestamp_format: Формат отображения временных меток
        """
        self.include_timestamps = include_timestamps
        self.include_speakers = include_speakers
        self.speaker_format = speaker_format
        self.timestamp_format = timestamp_format
    
    def format(
        self,
        result: TranscriptionResult,
        output_format: OutputFormat = "txt",
    ) -> str:
        """
        Форматирование результата в указанный формат.
        
        Args:
            result: Результат транскрипции
            output_format: Формат вывода
            
        Returns:
            Отформатированная строка
        """
        formatters = {
            "txt": self._format_txt,
            "json": self._format_json,
            "srt": self._format_srt,
            "vtt": self._format_vtt,
        }
        
        formatter = formatters.get(output_format)
        if formatter is None:
            raise ValueError(f"Неизвестный формат: {output_format}")
        
        return formatter(result)
    
    def _format_txt(self, result: TranscriptionResult) -> str:
        """Форматирование в текст."""
        return result.to_txt(
            include_timestamps=self.include_timestamps,
            include_speakers=self.include_speakers,
        )
    
    def _format_json(self, result: TranscriptionResult) -> str:
        """Форматирование в JSON."""
        return result.to_json()
    
    def _format_srt(self, result: TranscriptionResult) -> str:
        """Форматирование в SRT."""
        return result.to_srt()
    
    def _format_vtt(self, result: TranscriptionResult) -> str:
        """Форматирование в WebVTT."""
        return result.to_vtt()


class TranscriptFormatter:
    """Продвинутый форматтер для красивого вывода транскрипции."""
    
    @staticmethod
    def format_dialogue(
        segments: List[TranscriptionSegment],
        line_width: int = 80,
        show_timestamps: bool = True,
    ) -> str:
        """
        Форматирование в виде диалога.
        
        Пример:
        ═══════════════════════════════════════════════════════════════════════════════
        [00:00:00 - 00:00:15]
        Спикер №1: Привет, как дела? Давно не виделись, рад тебя видеть.
        
        [00:00:15 - 00:00:25]
        Спикер №2: Привет! Всё отлично, спасибо. А у тебя как?
        ═══════════════════════════════════════════════════════════════════════════════
        """
        lines = ["═" * line_width]
        
        for seg in segments:
            if show_timestamps:
                start_str = _format_time_txt(seg.start)
                end_str = _format_time_txt(seg.end)
                lines.append(f"[{start_str} - {end_str}]")
            
            if seg.speaker:
                lines.append(f"{seg.speaker}: {seg.text}")
            else:
                lines.append(seg.text)
            
            lines.append("")  # Пустая строка между репликами
        
        lines.append("═" * line_width)
        return "\n".join(lines)
    
    @staticmethod
    def format_screenplay(
        segments: List[TranscriptionSegment],
        uppercase_speakers: bool = True,
    ) -> str:
        """
        Форматирование в стиле сценария.
        
        Пример:
                        СПИКЕР №1
        Привет, как дела?
        
                        СПИКЕР №2
        Отлично, спасибо!
        """
        lines = []
        
        for seg in segments:
            speaker = seg.speaker or "ГОВОРЯЩИЙ"
            if uppercase_speakers:
                speaker = speaker.upper()
            
            # Спикер по центру (примерно)
            lines.append(f"                    {speaker}")
            lines.append(seg.text)
            lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_markdown(
        result: TranscriptionResult,
        include_metadata: bool = True,
    ) -> str:
        """
        Форматирование в Markdown.
        
        Пример:
        # Транскрипция
        
        **Источник:** video.mp4  
        **Длительность:** 01:23:45  
        **Модель:** v3_e2e_rnnt  
        
        ## Текст
        
        > **Спикер №1** (00:00 - 00:15):  
        > Привет, как дела?
        
        > **Спикер №2** (00:15 - 00:25):  
        > Отлично, спасибо!
        """
        lines = ["# Транскрипция", ""]
        
        if include_metadata:
            source = result.metadata.get("source", "неизвестно")
            duration_str = _format_time_txt(result.duration)
            
            lines.extend([
                f"**Источник:** {source}  ",
                f"**Длительность:** {duration_str}  ",
                f"**Модель:** {result.model_name}  ",
                f"**Время обработки:** {result.processing_time:.1f}с  ",
                "",
                "## Текст",
                "",
            ])
        
        for seg in result.segments:
            start_str = _format_time_txt(seg.start)
            end_str = _format_time_txt(seg.end)
            
            if seg.speaker:
                lines.append(f"> **{seg.speaker}** ({start_str} - {end_str}):  ")
            else:
                lines.append(f"> ({start_str} - {end_str}):  ")
            
            lines.append(f"> {seg.text}")
            lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_table(
        segments: List[TranscriptionSegment],
        delimiter: str = "|",
    ) -> str:
        """
        Форматирование в виде таблицы (TSV/CSV-подобный).
        
        Пример:
        Start|End|Speaker|Text
        00:00:00|00:00:15|Спикер №1|Привет, как дела?
        00:00:15|00:00:25|Спикер №2|Отлично, спасибо!
        """
        lines = [f"Start{delimiter}End{delimiter}Speaker{delimiter}Text"]
        
        for seg in segments:
            start_str = _format_time_txt(seg.start)
            end_str = _format_time_txt(seg.end)
            speaker = seg.speaker or ""
            text = seg.text.replace(delimiter, " ").replace("\n", " ")
            
            lines.append(f"{start_str}{delimiter}{end_str}{delimiter}{speaker}{delimiter}{text}")
        
        return "\n".join(lines)


def format_output(
    result: TranscriptionResult,
    output_format: OutputFormat = "txt",
    **kwargs
) -> str:
    """
    Форматирование результата транскрипции.
    
    Args:
        result: Результат транскрипции
        output_format: Формат вывода ("txt", "json", "srt", "vtt")
        **kwargs: Дополнительные параметры форматирования
        
    Returns:
        Отформатированная строка
    """
    formatter = OutputFormatter(**kwargs)
    return formatter.format(result, output_format)


def save_result(
    result: TranscriptionResult,
    path: Path | str,
    output_format: Optional[OutputFormat] = None,
) -> Path:
    """
    Сохранение результата в файл.
    
    Args:
        result: Результат транскрипции
        path: Путь к файлу
        output_format: Формат вывода (если None, определяется по расширению)
        
    Returns:
        Путь к сохранённому файлу
    """
    path = Path(path)
    
    if output_format is None:
        # Определение по расширению
        ext_map = {
            ".txt": "txt",
            ".json": "json",
            ".srt": "srt",
            ".vtt": "vtt",
        }
        output_format = ext_map.get(path.suffix.lower(), "txt")
    
    return result.save(path, output_format)
