"""
Основной класс GigaAMTranscriber - фасад для работы с GigaAM.

Обеспечивает:
- Транскрипцию аудио и видео файлов любой длительности
- Опциональную диаризацию спикеров
- Различные форматы вывода
"""

import hashlib
import logging
import os
import sys
import tempfile
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

import numpy as np

from .audio_processor import AudioProcessor
from .data_models import (
    DiarizationMode,
    OutputFormat,
    SpeakerSegment,
    TranscriptionResult,
    TranscriptionSegment,
)
from .diarization import DiarizationManager
from .exceptions import (
    AudioProcessingError,
    DiarizationError,
    EmptyAudioError,
    EmptyFileError,
    HFTokenMissingError,
    ModelLoadError,
    TranscriberError,
    UnsupportedFormatError,
)
from .formatters import format_output, save_result
from .segment_merger import MergeConfig, SegmentMerger, merge_segments

logger = logging.getLogger(__name__)

# Добавляем путь к GigaAM в PYTHONPATH
GIGAAM_PATH = Path(__file__).parent.parent / "GigaAM"
if str(GIGAAM_PATH) not in sys.path:
    sys.path.insert(0, str(GIGAAM_PATH))


class GigaAMTranscriber:
    """
    Фасад для работы с GigaAM транскрипцией.
    
    Принципы:
    - Lazy loading моделей (загружаются при первом использовании)
    - Единообразный интерфейс для audio/video
    - Прозрачная обработка любой длительности
    - Graceful degradation при отсутствии HF_TOKEN
    
    Примеры использования:
    
    >>> # Простая транскрипция
    >>> transcriber = GigaAMTranscriber()
    >>> result = transcriber.transcribe("audio.wav")
    >>> print(result.text)
    
    >>> # С диаризацией
    >>> result = transcriber.transcribe("meeting.mp4", diarization="pyannote")
    >>> for seg in result.segments:
    ...     print(f"{seg.speaker}: {seg.text}")
    
    >>> # Сохранение в файл
    >>> result.save("transcript.json", format="json")
    """
    
    # Ограничение GigaAM для метода transcribe()
    MAX_SHORT_DURATION = 25.0  # секунд
    
    def __init__(
        self,
        model_name: str = "v3_e2e_rnnt",
        device: str = "auto",
        hf_token: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        verbose: bool = False,
        fp16_encoder: bool = True,
    ):
        """
        Инициализация транскрибера.
        
        Args:
            model_name: Имя модели GigaAM ("v3_e2e_rnnt", "v3_e2e_ctc", и т.д.)
            device: Устройство ("auto", "cuda", "cpu")
            hf_token: HuggingFace токен для pyannote диаризации
            cache_dir: Директория для кэша
            verbose: Подробный вывод
            fp16_encoder: Использовать FP16 для энкодера (быстрее на GPU)
        """
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "gigaam_transcriber"
        self.verbose = verbose
        self.fp16_encoder = fp16_encoder
        
        # Lazy-loaded компоненты
        self._model = None
        self._audio_processor = None
        self._diarization_manager = None
        
        # Создание директории кэша
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Настройка логирования
        if verbose:
            logging.basicConfig(level=logging.DEBUG)
        
        logger.info(f"GigaAMTranscriber инициализирован: model={model_name}, device={self.device}")
    
    def _resolve_device(self, device: str) -> str:
        """Определение устройства."""
        if device == "auto":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return device
    
    # =========================================================================
    # Свойства с ленивой загрузкой
    # =========================================================================
    
    @property
    def model(self):
        """GigaAM модель (ленивая загрузка)."""
        if self._model is None:
            self._model = self._load_model()
        return self._model
    
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
                device=self.device,
            )
        return self._diarization_manager
    
    def _load_model(self):
        """Загрузка GigaAM модели."""
        try:
            import gigaam
        except ImportError:
            raise ModelLoadError(
                self.model_name,
                cause=ImportError(
                    "gigaam не установлен. "
                    "Установите: pip install -e ./GigaAM"
                )
            )
        
        try:
            logger.info(f"Загрузка модели {self.model_name}...")
            model = gigaam.load_model(
                self.model_name,
                fp16_encoder=self.fp16_encoder,
                device=self.device,
            )
            logger.info(f"Модель {self.model_name} загружена успешно")
            return model
        except Exception as e:
            raise ModelLoadError(self.model_name, cause=e)
    
    # =========================================================================
    # Контекстный менеджер
    # =========================================================================
    
    def __enter__(self):
        """Вход в контекст."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекста - освобождение ресурсов."""
        self.cleanup()
    
    def cleanup(self):
        """Освобождение GPU памяти и ресурсов."""
        if self._model is not None:
            del self._model
            self._model = None
            
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        
        logger.info("Ресурсы освобождены")
    
    # =========================================================================
    # Валидация
    # =========================================================================
    
    def _validate_input(self, path: Path) -> None:
        """Валидация входного файла."""
        if not path.exists():
            raise FileNotFoundError(str(path))
        
        if path.stat().st_size == 0:
            raise EmptyFileError(str(path))
        
        if not self.audio_processor.is_supported_file(path):
            raise UnsupportedFormatError(path.suffix)
    
    # =========================================================================
    # Основные методы транскрипции
    # =========================================================================
    
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
    ) -> TranscriptionResult:
        """
        Универсальный метод транскрипции.
        
        Автоматически определяет тип файла (audio/video) и выбирает
        оптимальную стратегию обработки.
        
        Args:
            input_path: Путь к входному файлу (аудио или видео)
            output_path: Путь для сохранения результата (опционально)
            diarization: Режим диаризации ("none", "pyannote", "hybrid")
            num_speakers: Точное количество спикеров (если известно)
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
            language: Язык ("ru")
            output_format: Формат вывода ("txt", "json", "srt", "vtt")
            merge_same_speaker: Объединять смежные реплики одного спикера
            min_segment_gap: Минимальный gap для объединения (секунды)
            
        Returns:
            TranscriptionResult с текстом, сегментами и метаданными
        """
        input_path = Path(input_path)
        start_time = time.time()
        
        # Валидация
        self._validate_input(input_path)
        
        logger.info(f"Начало транскрипции: {input_path}")
        
        # Graceful degradation для диаризации
        if diarization != "none" and self.hf_token is None:
            warnings.warn(
                "HF_TOKEN не установлен, диаризация будет пропущена. "
                "Установите переменную окружения HF_TOKEN для диаризации."
            )
            diarization = "none"
        
        # Определяем тип файла и вызываем соответствующий метод
        if self.audio_processor.is_video_file(input_path):
            result = self._transcribe_video(
                input_path,
                diarization=diarization,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
        else:
            result = self._transcribe_audio(
                input_path,
                diarization=diarization,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
        
        # Сшивка сегментов
        if merge_same_speaker and result.segments:
            merger = SegmentMerger(MergeConfig(max_gap=min_segment_gap))
            result.segments = merger.merge_same_speaker_segments(
                result.segments, 
                max_gap=min_segment_gap
            )
            # Обновляем полный текст
            result.text = " ".join(seg.text for seg in result.segments)
        
        # Обновление метаданных
        processing_time = time.time() - start_time
        result.processing_time = processing_time
        result.language = language
        result.model_name = self.model_name
        result.metadata["source"] = str(input_path)
        
        logger.info(
            f"Транскрипция завершена за {processing_time:.1f}с "
            f"({len(result.segments)} сегментов)"
        )
        
        # Сохранение результата
        if output_path:
            save_result(result, output_path, output_format)
            logger.info(f"Результат сохранён: {output_path}")
        
        return result
    
    def _transcribe_audio(
        self,
        audio_path: Path,
        diarization: DiarizationMode = "none",
        **diarization_kwargs,
    ) -> TranscriptionResult:
        """Внутренний метод транскрипции аудио."""
        # Подготовка аудио (конвертация в нужный формат)
        temp_audio = None
        try:
            if audio_path.suffix.lower() != ".wav":
                temp_audio = self.audio_processor.prepare_for_gigaam(audio_path)
                working_audio = temp_audio
            else:
                # Проверяем параметры WAV
                info = self.audio_processor.get_media_info(audio_path)
                if (info.get("sample_rate") != 16000 or 
                    info.get("channels") != 1):
                    temp_audio = self.audio_processor.normalize(audio_path)
                    working_audio = temp_audio
                else:
                    working_audio = audio_path
            
            # Получаем длительность
            duration = self.audio_processor.get_duration(working_audio)
            
            # Транскрипция
            if duration <= self.MAX_SHORT_DURATION:
                segments = self._transcribe_short(working_audio)
            else:
                segments = self._transcribe_long(working_audio)
            
            if not segments:
                raise EmptyAudioError(str(audio_path))
            
            # Диаризация
            if diarization != "none":
                segments = self._apply_diarization(
                    working_audio,
                    segments,
                    mode=diarization,
                    **diarization_kwargs,
                )
            
            # Формирование результата
            full_text = " ".join(seg.text for seg in segments)
            
            return TranscriptionResult(
                text=full_text,
                segments=segments,
                duration=duration,
                language="ru",
                model_name=self.model_name,
                processing_time=0,  # Будет обновлено в transcribe()
                metadata={"source": str(audio_path)},
            )
            
        finally:
            # Удаление временного файла
            if temp_audio and temp_audio != audio_path and temp_audio.exists():
                try:
                    temp_audio.unlink()
                except Exception:
                    pass
    
    def _transcribe_video(
        self,
        video_path: Path,
        keep_temp_audio: bool = False,
        **kwargs,
    ) -> TranscriptionResult:
        """Внутренний метод транскрипции видео."""
        temp_audio = None
        try:
            # Извлечение аудио
            logger.info(f"Извлечение аудио из видео: {video_path}")
            temp_audio = self.audio_processor.extract_audio_from_video(
                video_path,
                normalize=True,
            )
            
            # Транскрипция извлечённого аудио
            result = self._transcribe_audio(temp_audio, **kwargs)
            result.metadata["source"] = str(video_path)
            result.metadata["source_type"] = "video"
            
            return result
            
        finally:
            if not keep_temp_audio and temp_audio and temp_audio.exists():
                try:
                    temp_audio.unlink()
                except Exception:
                    pass
    
    def _transcribe_short(self, audio_path: Path) -> List[TranscriptionSegment]:
        """Транскрипция короткого аудио (< 25 сек)."""
        logger.debug(f"Транскрипция короткого аудио: {audio_path}")
        
        text = self.model.transcribe(str(audio_path))
        duration = self.audio_processor.get_duration(audio_path)
        
        if not text or not text.strip():
            return []
        
        return [TranscriptionSegment(
            text=text.strip(),
            start=0.0,
            end=duration,
        )]
    
    def _transcribe_long(self, audio_path: Path) -> List[TranscriptionSegment]:
        """Транскрипция длинного аудио через transcribe_longform."""
        logger.debug(f"Транскрипция длинного аудио: {audio_path}")
        
        try:
            utterances = self.model.transcribe_longform(str(audio_path))
        except Exception as e:
            logger.error(f"Ошибка transcribe_longform: {e}")
            raise AudioProcessingError(
                f"Ошибка при транскрипции длинного файла: {e}",
                file_path=str(audio_path),
                cause=e,
            )
        
        segments = []
        for utt in utterances:
            text = utt["transcription"]
            start, end = utt["boundaries"]
            
            if text and text.strip():
                segments.append(TranscriptionSegment(
                    text=text.strip(),
                    start=start,
                    end=end,
                ))
        
        return segments
    
    def _apply_diarization(
        self,
        audio_path: Path,
        segments: List[TranscriptionSegment],
        mode: DiarizationMode,
        **kwargs,
    ) -> List[TranscriptionSegment]:
        """Применение диаризации к сегментам."""
        logger.info(f"Применение диаризации: mode={mode}")
        
        try:
            if mode == "pyannote":
                speaker_segments = self.diarization_manager.diarize(
                    audio_path,
                    **kwargs
                )
            elif mode == "hybrid":
                # Для гибридного режима используем VAD сегменты
                from .diarization import HybridDiarization
                hybrid = HybridDiarization(
                    hf_token=self.hf_token,
                    device=self.device,
                )
                speech_segments = [(s.start, s.end) for s in segments]
                speaker_segments = hybrid.diarize(
                    audio_path,
                    speech_segments,
                    num_speakers=kwargs.get("num_speakers"),
                )
            else:
                return segments
            
            # Сопоставление спикеров с транскрипцией
            segments = self.diarization_manager.map_speakers_to_transcription(
                segments,
                speaker_segments,
            )
            
            return segments
            
        except HFTokenMissingError:
            warnings.warn(
                "HF_TOKEN не установлен, диаризация пропущена."
            )
            return segments
        except Exception as e:
            logger.error(f"Ошибка диаризации: {e}")
            warnings.warn(f"Ошибка диаризации, продолжаем без неё: {e}")
            return segments
    
    # =========================================================================
    # Альтернативные методы
    # =========================================================================
    
    def audio2text(
        self,
        in_audio: Union[str, Path],
        out_text: Optional[Union[str, Path]] = None,
        diarization: DiarizationMode = "none",
        **kwargs,
    ) -> TranscriptionResult:
        """
        Транскрибация аудио файла.
        
        Поддерживает: WAV, FLAC, MP3, OGG, M4A, AAC и любые ffmpeg-совместимые форматы.
        
        Args:
            in_audio: Путь к аудио файлу
            out_text: Путь для сохранения результата
            diarization: Режим диаризации
            **kwargs: Дополнительные параметры для transcribe()
            
        Returns:
            TranscriptionResult
        """
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
        **kwargs,
    ) -> TranscriptionResult:
        """
        Транскрибация видео файла.
        
        Извлекает аудио через ffmpeg, затем транскрибирует.
        
        Args:
            in_video: Путь к видео файлу
            out_text: Путь для сохранения результата
            diarization: Режим диаризации
            keep_temp_audio: Сохранять временный аудио файл
            **kwargs: Дополнительные параметры для transcribe()
            
        Returns:
            TranscriptionResult
        """
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
        **kwargs,
    ) -> List[TranscriptionResult]:
        """
        Пакетная обработка нескольких файлов.
        
        Args:
            input_paths: Список путей к файлам
            output_dir: Директория для сохранения результатов
            diarization: Режим диаризации
            n_workers: Количество параллельных воркеров
            progress_callback: Callback для прогресса: (current, total, filename)
            **kwargs: Дополнительные параметры
            
        Returns:
            Список TranscriptionResult
        """
        results = []
        total = len(input_paths)
        
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
        
        # Последовательная обработка (GPU не параллелится)
        for i, input_path in enumerate(input_paths):
            input_path = Path(input_path)
            
            if progress_callback:
                progress_callback(i, total, input_path.name)
            
            logger.info(f"Обработка {i+1}/{total}: {input_path.name}")
            
            # Определение выходного пути
            output_path = None
            if output_dir:
                output_path = output_dir / f"{input_path.stem}.txt"
            
            try:
                result = self.transcribe(
                    input_path,
                    output_path=output_path,
                    diarization=diarization,
                    **kwargs,
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Ошибка при обработке {input_path}: {e}")
                # Создаём пустой результат с ошибкой
                results.append(TranscriptionResult(
                    text="",
                    segments=[],
                    duration=0,
                    language="ru",
                    model_name=self.model_name,
                    processing_time=0,
                    metadata={"source": str(input_path), "error": str(e)},
                ))
        
        if progress_callback:
            progress_callback(total, total, "Готово")
        
        return results
    
    def transcribe_stream(
        self,
        audio_iterator: Iterator[np.ndarray],
        sample_rate: int = 16000,
        chunk_duration: float = 20.0,
    ) -> Iterator[TranscriptionSegment]:
        """
        Потоковая транскрипция для real-time приложений.
        
        Args:
            audio_iterator: Итератор numpy массивов с аудио данными
            sample_rate: Частота дискретизации
            chunk_duration: Длительность чанка в секундах
            
        Yields:
            TranscriptionSegment для каждого обработанного чанка
        """
        import torch
        
        buffer = []
        buffer_duration = 0
        current_time = 0
        chunk_samples = int(chunk_duration * sample_rate)
        
        for chunk in audio_iterator:
            buffer.append(chunk)
            buffer_duration += len(chunk) / sample_rate
            
            # Когда накопилось достаточно данных
            while buffer_duration >= chunk_duration:
                # Собираем чанк
                audio_data = np.concatenate(buffer)
                process_samples = min(chunk_samples, len(audio_data))
                process_chunk = audio_data[:process_samples]
                
                # Сохраняем остаток
                remaining = audio_data[process_samples:]
                buffer = [remaining] if len(remaining) > 0 else []
                buffer_duration = len(remaining) / sample_rate
                
                # Транскрибируем
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    temp_path = Path(f.name)
                
                try:
                    import torchaudio
                    waveform = torch.from_numpy(process_chunk).unsqueeze(0).float()
                    torchaudio.save(str(temp_path), waveform, sample_rate)
                    
                    text = self.model.transcribe(str(temp_path))
                    
                    if text and text.strip():
                        segment_duration = len(process_chunk) / sample_rate
                        yield TranscriptionSegment(
                            text=text.strip(),
                            start=current_time,
                            end=current_time + segment_duration,
                        )
                        current_time += segment_duration
                finally:
                    if temp_path.exists():
                        temp_path.unlink()
        
        # Обработка остатка
        if buffer and buffer_duration > 0.5:  # Минимум 0.5 сек
            audio_data = np.concatenate(buffer)
            
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = Path(f.name)
            
            try:
                import torchaudio
                waveform = torch.from_numpy(audio_data).unsqueeze(0).float()
                torchaudio.save(str(temp_path), waveform, sample_rate)
                
                text = self.model.transcribe(str(temp_path))
                
                if text and text.strip():
                    yield TranscriptionSegment(
                        text=text.strip(),
                        start=current_time,
                        end=current_time + buffer_duration,
                    )
            finally:
                if temp_path.exists():
                    temp_path.unlink()
    
    # =========================================================================
    # Вспомогательные методы
    # =========================================================================
    
    def get_model_info(self) -> Dict[str, Any]:
        """Получить информацию о модели."""
        return {
            "model_name": self.model_name,
            "device": self.device,
            "loaded": self._model is not None,
            "hf_token_set": self.hf_token is not None,
            "cache_dir": str(self.cache_dir),
        }
    
    def preload(self) -> None:
        """Предзагрузка модели для ускорения первого запроса."""
        _ = self.model
        logger.info("Модель предзагружена")


def create_transcriber(
    model_name: str = "v3_e2e_rnnt",
    device: str = "auto",
    hf_token: Optional[str] = None,
    **kwargs,
) -> GigaAMTranscriber:
    """
    Создание транскрибера с заданными параметрами.
    
    Это фабричная функция для удобного создания GigaAMTranscriber.
    
    Args:
        model_name: Имя модели
        device: Устройство
        hf_token: HuggingFace токен
        **kwargs: Дополнительные параметры
        
    Returns:
        Настроенный GigaAMTranscriber
    """
    return GigaAMTranscriber(
        model_name=model_name,
        device=device,
        hf_token=hf_token,
        **kwargs,
    )
