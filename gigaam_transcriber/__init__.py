"""
GigaAM Transcriber - микробиблиотека для транскрипции аудио и видео.

Использует Mistral Voxtral API для транскрипции
с поддержкой диаризации спикеров через pyannote.

Примеры использования:

    >>> from gigaam_transcriber import GigaAMTranscriber

    >>> # Простая транскрипция
    >>> transcriber = GigaAMTranscriber(api_key="<MISTRAL_API_KEY>")
    >>> result = transcriber.transcribe("audio.wav")
    >>> print(result.text)

    >>> # С диаризацией
    >>> result = transcriber.transcribe("meeting.mp4", diarization="pyannote")
    >>> for seg in result.segments:
    ...     print(f"{seg.speaker}: {seg.text}")

    >>> # Сохранение в файл
    >>> result.save("transcript.json", format="json")

    >>> # Контекстный менеджер для освобождения ресурсов
    >>> with GigaAMTranscriber() as transcriber:
    ...     result = transcriber.transcribe("audio.wav")

ASR модели Mistral:
- voxtral-mini-latest (по умолчанию)

Режимы диаризации:
- "none" - без диаризации
- "pyannote" - полная диаризация через pyannote/speaker-diarization-3.1
- "hybrid" - легковесный подход: VAD + эмбеддинги + кластеризация

Форматы вывода:
- "txt" - текстовый формат с временными метками
- "json" - полный JSON с метаданными
- "srt" - субтитры SubRip
- "vtt" - субтитры WebVTT
"""

__version__ = "0.1.0"
__author__ = "GigaAM Transcriber"

# Основной класс
from .transcriber import GigaAMTranscriber, create_transcriber
from .mistral_client import MistralASRClient

# Структуры данных
from .data_models import (
    DiarizationMode,
    OutputFormat,
    TranscriptionResult,
    TranscriptionSegment,
    WordSegment,
    SpeakerSegment,
)

# Исключения
from .exceptions import (
    TranscriberError,
    AudioTooShortError,
    AudioTooLongError,
    UnsupportedFormatError,
    DiarizationError,
    HFTokenMissingError,
    ModelLoadError,
    AudioProcessingError,
    ASRError,
    FFmpegNotFoundError,
    EmptyAudioError,
    EmptyFileError,
)

# Вспомогательные модули
from .audio_processor import AudioProcessor
from .diarization import DiarizationManager
from .segment_merger import SegmentMerger, MergeConfig, merge_segments
from .formatters import OutputFormatter, TranscriptFormatter, format_output, save_result

__all__ = [
    # Версия
    "__version__",
    # Основной класс
    "GigaAMTranscriber",
    "create_transcriber",
    "MistralASRClient",
    # Структуры данных
    "DiarizationMode",
    "OutputFormat",
    "TranscriptionResult",
    "TranscriptionSegment",
    "WordSegment",
    "SpeakerSegment",
    # Исключения
    "TranscriberError",
    "AudioTooShortError",
    "AudioTooLongError",
    "UnsupportedFormatError",
    "DiarizationError",
    "HFTokenMissingError",
    "ModelLoadError",
    "AudioProcessingError",
    "ASRError",
    "FFmpegNotFoundError",
    "EmptyAudioError",
    "EmptyFileError",
    # Вспомогательные классы
    "AudioProcessor",
    "DiarizationManager",
    "SegmentMerger",
    "MergeConfig",
    "merge_segments",
    "OutputFormatter",
    "TranscriptFormatter",
    "format_output",
    "save_result",
]


# Удобная функция для быстрого старта
def transcribe(
    input_path: str,
    output_path: str | None = None,
    diarization: DiarizationMode = "none",
    api_key: str | None = None,
    **kwargs,
) -> TranscriptionResult:
    """
    Быстрая транскрипция файла.

    Это удобная функция для быстрого использования без создания
    экземпляра GigaAMTranscriber.

    Args:
        input_path: Путь к аудио или видео файлу
        output_path: Путь для сохранения результата (опционально)
        diarization: Режим диаризации ("none", "pyannote", "hybrid")
        api_key: API ключ Mistral (если не указан, берётся из MISTRAL_API_KEY)
        **kwargs: Дополнительные параметры

    Returns:
        TranscriptionResult с текстом и сегментами

    Пример:
        >>> from gigaam_transcriber import transcribe
        >>> result = transcribe("meeting.mp4", diarization="pyannote")
        >>> print(result.text)
    """
    with GigaAMTranscriber(api_key=api_key, **kwargs) as t:
        return t.transcribe(
            input_path,
            output_path=output_path,
            diarization=diarization,
        )
