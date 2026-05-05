"""
Основной класс GigaAMTranscriber - фасад для работы с Mistral Voxtral API.

Обеспечивает:
- Транскрипцию аудио и видео файлов любой длительности
- Опциональную диаризацию спикеров
- Различные форматы вывода
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .audio_processor import AudioProcessor
from .data_models import (
    DiarizationMode,
    OutputFormat,
    SpeakerSegment,
    TranscriptionResult,
    TranscriptionSegment,
)
from .diarization import DiarizationManager, HybridDiarization
from .exceptions import (
    EmptyAudioError,
    EmptyFileError,
    UnsupportedFormatError,
)
from .formatters import save_result
from .mistral_client import MistralASRClient
from .segment_merger import MergeConfig, SegmentMerger

logger = logging.getLogger(__name__)

CHUNK_THRESHOLD_SEC = 1800.0
CHUNK_DURATION_SEC = 300.0


class GigaAMTranscriber:
    """
    Фасад транскрипции с использованием Mistral Voxtral API.

    Класс сохраняет имя для обратной совместимости,
    но внутри использует облачный ASR вместо локальной GigaAM модели.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        asr_url: str = "https://api.mistral.ai",
        asr_model: str = "voxtral-mini-latest",
        hf_token: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        verbose: bool = False,
        proxy: Optional[str] = None,
        chunk_threshold: float = CHUNK_THRESHOLD_SEC,
        chunk_duration: float = CHUNK_DURATION_SEC,
        min_request_interval: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY", "")
        self.asr_url = asr_url
        self.asr_model = asr_model
        self.proxy = proxy or os.getenv("PROXY_URL")
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.cache_dir = (
            Path(cache_dir) if cache_dir else Path.home() / ".cache" / "gigaam_transcriber"
        )
        self.verbose = verbose
        self.chunk_threshold = chunk_threshold
        self.chunk_duration = chunk_duration
        self._min_request_interval = min_request_interval

        self._audio_processor: Optional[AudioProcessor] = None
        self._diarization_manager: Optional[DiarizationManager] = None
        self._asr_client: Optional[MistralASRClient] = None

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if verbose:
            logging.basicConfig(level=logging.DEBUG)

        logger.info(
            "GigaAMTranscriber инициализирован: asr_model=%s, asr_url=%s",
            self.asr_model,
            self.asr_url,
        )

    @property
    def audio_processor(self) -> AudioProcessor:
        """Процессор аудио (ленивая загрузка)."""
        if self._audio_processor is None:
            self._audio_processor = AudioProcessor()
        return self._audio_processor

    @property
    def diarization_manager(self) -> DiarizationManager:
        """Менеджер диаризации (ленивая загрузка)."""
        if self._diarization_manager is None:
            self._diarization_manager = DiarizationManager(
                hf_token=self.hf_token,
                device="auto",
            )
        return self._diarization_manager

    @property
    def asr_client(self) -> MistralASRClient:
        if self._asr_client is None:
            self._asr_client = MistralASRClient(
                asr_url=self.asr_url,
                model=self.asr_model,
                api_key=self.api_key or "",
                proxy=self.proxy,
                min_request_interval=self._min_request_interval,
            )
        return self._asr_client

    def __enter__(self) -> "GigaAMTranscriber":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Освобождение ресурсов (HTTP-клиент и временные объекты)."""
        if self._asr_client is not None:
            self._asr_client.close()
            self._asr_client = None
        logger.info("Ресурсы освобождены")

    def _validate_input(self, path: Path) -> None:
        """Валидация входного файла."""
        if not path.exists():
            raise FileNotFoundError(str(path))

        if path.stat().st_size == 0:
            raise EmptyFileError(str(path))

        if not self.audio_processor.is_supported_file(path):
            raise UnsupportedFormatError(path.suffix)

    def _prepare_audio(self, audio_path: Path, denoise: str = "none") -> tuple[Path, Optional[Path]]:
        temp_audio: Optional[Path] = None

        if audio_path.suffix.lower() != ".wav":
            temp_audio = self.audio_processor.normalize(audio_path, denoise=denoise)
            return temp_audio, temp_audio

        info = self.audio_processor.get_media_info(audio_path)
        if info.get("sample_rate") != 16000 or info.get("channels") != 1:
            temp_audio = self.audio_processor.normalize(audio_path, denoise=denoise)
            return temp_audio, temp_audio

        return audio_path, None

    def _get_speaker_segments(
        self,
        audio_path: Path,
        mode: DiarizationMode,
        base_segments: Optional[List[TranscriptionSegment]] = None,
        **kwargs: Any,
    ) -> List[SpeakerSegment]:
        """Получить сегменты спикеров для выбранного режима диаризации."""
        if mode == "pyannote":
            return self.diarization_manager.diarize(audio_path, **kwargs)

        if mode == "hybrid":
            hybrid = HybridDiarization(hf_token=self.hf_token, device="auto")
            speech_segments = (
                [(s.start, s.end) for s in base_segments]
                if base_segments
                else self.audio_processor.split_by_silence(audio_path)
            )
            return hybrid.diarize(
                audio_path,
                speech_segments,
                num_speakers=kwargs.get("num_speakers"),
            )

        return []

    def transcribe(
        self,
        input_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        language: str = "ru",
        output_format: OutputFormat = "txt",
        merge_same_speaker: bool = True,
        min_segment_gap: float = 0.5,
        denoise: str = "none",
    ) -> TranscriptionResult:
        """Универсальный метод транскрипции аудио/видео."""
        input_path = Path(input_path)
        start_time = time.time()

        self._validate_input(input_path)
        logger.info("Начало транскрипции: %s", input_path)

        if diarization != "none" and self.hf_token is None:
            warnings.warn(
                "HF_TOKEN не установлен, диаризация будет пропущена. "
                "Установите переменную окружения HF_TOKEN для диаризации."
            )
            diarization = "none"

        diarization_kwargs = {
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
        }

        if self.audio_processor.is_video_file(input_path):
            result = self._transcribe_video(
                input_path,
                diarization=diarization,
                keep_temp_audio=False,
                denoise=denoise,
                **diarization_kwargs,
            )
        else:
            result = self._transcribe_audio(
                input_path,
                diarization=diarization,
                denoise=denoise,
                **diarization_kwargs,
            )

        if merge_same_speaker and result.segments:
            merger = SegmentMerger(MergeConfig(max_gap=min_segment_gap))
            result.segments = merger.merge_same_speaker_segments(
                result.segments,
                max_gap=min_segment_gap,
            )
            result.text = " ".join(seg.text for seg in result.segments)

        processing_time = time.time() - start_time
        result.processing_time = processing_time
        result.language = language
        result.model_name = self.asr_model
        result.metadata["source"] = str(input_path)
        result.metadata["asr_url"] = self.asr_url

        logger.info(
            "Транскрипция завершена за %.1fс (%d сегментов)",
            processing_time,
            len(result.segments),
        )

        if output_path:
            save_result(result, output_path, output_format)
            logger.info("Результат сохранён: %s", output_path)

        return result

    def _transcribe_audio(
        self,
        audio_path: Path,
        diarization: DiarizationMode = "none",
        denoise: str = "none",
        **diarization_kwargs: Any,
    ) -> TranscriptionResult:
        """Внутренний метод транскрипции аудио через Mistral API."""
        temp_audio: Optional[Path] = None
        try:
            working_audio, temp_audio = self._prepare_audio(audio_path, denoise=denoise)
            duration = self.audio_processor.get_duration(working_audio)

            segments: List[TranscriptionSegment]
            if diarization == "none":
                if duration >= self.chunk_threshold:
                    text = self._transcribe_chunked(working_audio, duration)
                else:
                    text = self.asr_client.transcribe(str(working_audio)).strip()
                if not text:
                    raise EmptyAudioError(str(audio_path))
                segments = [
                    TranscriptionSegment(
                        text=text,
                        start=0.0,
                        end=duration,
                    )
                ]
            else:
                speaker_segments = self._get_speaker_segments(
                    working_audio,
                    mode=diarization,
                    **diarization_kwargs,
                )
                # Предтранскрипционное объединение сегментов для предотвращения галлюцинаций ASR
                merger = SegmentMerger(MergeConfig(min_presplit_duration=1.0))
                speaker_segments = merger.merge_speaker_segments(speaker_segments)
                speaker_segments = merger.merge_short_speaker_segments(speaker_segments)
                segment_dicts = [
                    {"start": seg.start, "end": seg.end, "speaker": seg.speaker}
                    for seg in speaker_segments
                ]
                api_segments = self.asr_client.transcribe_segments(
                    str(working_audio),
                    segment_dicts,
                )
                segments = [
                    TranscriptionSegment(
                        text=seg.text.strip(),
                        start=seg.start,
                        end=seg.end,
                        speaker=seg.speaker,
                    )
                    for seg in api_segments
                    if seg.text and seg.text.strip()
                ]
                if not segments:
                    raise EmptyAudioError(str(audio_path))

            full_text = " ".join(seg.text for seg in segments)
            return TranscriptionResult(
                text=full_text,
                segments=segments,
                duration=duration,
                language="ru",
                model_name=self.asr_model,
                processing_time=0,
                metadata={"source": str(audio_path)},
            )
        finally:
            if temp_audio and temp_audio != audio_path and temp_audio.exists():
                try:
                    temp_audio.unlink()
                except Exception:
                    pass

    def _transcribe_chunked(self, audio_path: Path, duration: float) -> str:
        chunks = self.audio_processor.split_audio(
            audio_path,
            chunk_duration=self.chunk_duration,
        )
        chunk_files = [chunk[0] for chunk in chunks]
        try:

            def _transcribe_one(idx: int, chunk_path: Path) -> tuple[int, str]:
                text = self.asr_client.transcribe(str(chunk_path))
                return idx, text.strip() if text else ""

            texts: List[tuple[int, str]] = []
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(_transcribe_one, i, path): i
                    for i, path in enumerate(chunk_files)
                }
                for future in as_completed(futures):
                    idx, text = future.result()
                    if text:
                        texts.append((idx, text))

            texts.sort(key=lambda x: x[0])
            return " ".join(text for _, text in texts)
        finally:
            for chunk_file in chunk_files:
                try:
                    chunk_file.unlink()
                except Exception:
                    pass

    def _transcribe_video(
        self,
        video_path: Path,
        keep_temp_audio: bool = False,
        **kwargs: Any,
    ) -> TranscriptionResult:
        temp_audio: Optional[Path] = None
        denoise = kwargs.pop("denoise", "none")
        try:
            logger.info("Извлечение аудио из видео: %s", video_path)
            temp_audio = self.audio_processor.extract_audio_from_video(
                video_path,
                normalize=True,
            )

            result = self._transcribe_audio(temp_audio, denoise=denoise, **kwargs)
            result.metadata["source"] = str(video_path)
            result.metadata["source_type"] = "video"
            return result
        finally:
            if not keep_temp_audio and temp_audio and temp_audio.exists():
                try:
                    temp_audio.unlink()
                except Exception:
                    pass

    def _apply_diarization(
        self,
        audio_path: Path,
        segments: List[TranscriptionSegment],
        mode: DiarizationMode,
        **kwargs: Any,
    ) -> List[TranscriptionSegment]:
        """Совместимый метод: пере-сопоставление спикеров по готовым сегментам."""
        if mode == "none" or not segments:
            return segments

        speaker_segments = self._get_speaker_segments(
            audio_path,
            mode=mode,
            base_segments=segments,
            **kwargs,
        )
        if not speaker_segments:
            return segments

        return self.diarization_manager.map_speakers_to_transcription(
            segments,
            speaker_segments,
        )

    def audio2text(
        self,
        in_audio: Union[str, Path],
        out_text: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Транскрибация аудио файла."""
        return self.transcribe(
            in_audio,
            output_path=out_text,
            diarization=diarization,
            **kwargs,
        )

    def video2text(
        self,
        in_video: Union[str, Path],
        out_text: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        keep_temp_audio: bool = False,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Транскрибация видео файла."""
        _ = keep_temp_audio  # Для совместимости сигнатуры
        return self.transcribe(
            in_video,
            output_path=out_text,
            diarization=diarization,
            **kwargs,
        )

    def transcribe_batch(
        self,
        input_paths: List[Union[str, Path]],
        output_dir: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        n_workers: int = 1,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        **kwargs: Any,
    ) -> List[TranscriptionResult]:
        """Пакетная обработка нескольких файлов (с поддержкой потоков)."""
        total = len(input_paths)
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        ordered_paths = [Path(p) for p in input_paths]
        results: List[Optional[TranscriptionResult]] = [None] * total

        def _process_one(idx: int, input_path: Path) -> tuple[int, TranscriptionResult]:
            output_path = output_dir / f"{input_path.stem}.txt" if output_dir else None
            try:
                result = self.transcribe(
                    input_path,
                    output_path=output_path,
                    diarization=diarization,
                    **kwargs,
                )
                return idx, result
            except Exception as e:
                logger.error("Ошибка при обработке %s: %s", input_path, e)
                return idx, TranscriptionResult(
                    text="",
                    segments=[],
                    duration=0,
                    language="ru",
                    model_name=self.asr_model,
                    processing_time=0,
                    metadata={"source": str(input_path), "error": str(e)},
                )

        if n_workers <= 1:
            for i, input_path in enumerate(ordered_paths):
                if progress_callback:
                    progress_callback(i, total, input_path.name)
                idx, result = _process_one(i, input_path)
                results[idx] = result
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures = {
                    executor.submit(_process_one, i, input_path): i
                    for i, input_path in enumerate(ordered_paths)
                }
                done_count = 0
                for future in as_completed(futures):
                    idx, result = future.result()
                    results[idx] = result
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, total, ordered_paths[idx].name)

        if progress_callback:
            progress_callback(total, total, "Готово")

        return [r for r in results if r is not None]

    def get_model_info(self) -> Dict[str, Any]:
        """Получить информацию о текущей ASR конфигурации."""
        return {
            "model_name": self.asr_model,
            "asr_url": self.asr_url,
            "provider": "mistral",
            "loaded": self._asr_client is not None,
            "hf_token_set": self.hf_token is not None,
            "api_key_set": bool(self.api_key),
            "cache_dir": str(self.cache_dir),
        }


def create_transcriber(
    api_key: Optional[str] = None,
    asr_url: str = "https://api.mistral.ai",
    asr_model: str = "voxtral-mini-latest",
    hf_token: Optional[str] = None,
    **kwargs: Any,
) -> GigaAMTranscriber:
    """Фабричная функция создания GigaAMTranscriber (на Mistral API)."""
    return GigaAMTranscriber(
        api_key=api_key,
        asr_url=asr_url,
        asr_model=asr_model,
        hf_token=hf_token,
        **kwargs,
    )
