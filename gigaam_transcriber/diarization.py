"""
Модуль диаризации спикеров для GigaAM Transcriber.

Поддерживает:
- pyannote: Полная диаризация через pyannote/speaker-diarization-3.1
- hybrid: Гибридный подход с VAD + эмбеддинги + кластеризация
"""

import logging
import os
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

from .data_models import SpeakerSegment, TranscriptionSegment
from .exceptions import DiarizationError, HFTokenMissingError

logger = logging.getLogger(__name__)

# Кэш для загруженных моделей
_diarization_pipeline = None
_embedding_model = None


class DiarizationManager:
    """Менеджер диаризации спикеров."""
    
    def __init__(
        self,
        hf_token: Optional[str] = None,
        device: str = "auto",
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ):
        """
        Инициализация менеджера диаризации.
        
        Args:
            hf_token: HuggingFace токен для доступа к pyannote моделям
            device: Устройство ("auto", "cuda", "cpu")
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
        """
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.device = self._resolve_device(device)
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        
        self._pipeline = None
    
    def _resolve_device(self, device: str) -> str:
        """Определение устройства."""
        if device == "auto":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return device
    
    @property
    def pipeline(self):
        """Ленивая загрузка pipeline диаризации."""
        if self._pipeline is None:
            self._pipeline = self._load_pipeline()
        return self._pipeline
    
    def _load_pipeline(self):
        """Загрузка pyannote pipeline."""
        if not self.hf_token:
            raise HFTokenMissingError()
        
        try:
            from pyannote.audio import Pipeline
            import torch
        except ImportError:
            raise DiarizationError(
                "pyannote.audio не установлен. "
                "Установите: pip install pyannote.audio"
            )
        
        try:
            import torch.serialization
            from torch.torch_version import TorchVersion
            safe_globals_to_add = [TorchVersion]
            try:
                from pyannote.audio.core.task import (
                    Specifications, Problem, Task, Resolution, Scope,
                    UnknownSpecificationsError,
                )
                safe_globals_to_add.extend([
                    Specifications, Problem, Task, Resolution, Scope,
                    UnknownSpecificationsError,
                ])
            except ImportError:
                pass
            torch.serialization.add_safe_globals(safe_globals_to_add)
        except (ImportError, AttributeError):
            pass
        
        # Устанавливаем токен для huggingface_hub
        try:
            from huggingface_hub import login
            # Пробуем логин, если токен не установлен в окружении
            if not os.getenv("HF_TOKEN"):
                login(token=self.hf_token, add_to_git_credential=False)
        except Exception as e:
            logger.debug(f"Не удалось установить токен через huggingface_hub: {e}")
        
        # Список моделей для попытки загрузки
        models_to_try = [
            "pyannote/speaker-diarization-3.1",
            "pyannote/speaker-diarization",
        ]
        
        last_error = None
        
        pipeline = None
        for model_id in models_to_try:
            try:
                logger.info(f"Попытка загрузки модели: {model_id}")
                # В новых версиях pyannote.audio используется 'token' вместо 'use_auth_token'
                try:
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        token=self.hf_token
                    )
                except TypeError:
                    # Fallback для старых версий
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        use_auth_token=self.hf_token
                    )
                
                if pipeline is not None:
                    logger.info(f"Модель {model_id} загружена успешно")
                    break
                else:
                    logger.warning(
                        f"Модель {model_id} вернула None. "
                        f"Возможно, не принято соглашение на HuggingFace или токен недействителен."
                    )
                    continue
            except Exception as e:
                last_error = e
                error_str = str(e)
                
                # Проверяем на ошибку доступа
                if "403" in error_str or "gated" in error_str.lower() or "authorized" in error_str.lower():
                    logger.warning(
                        f"Нет доступа к модели {model_id}. "
                        f"Необходимо принять условия использования на HuggingFace:\n"
                        f"1. Перейдите на https://huggingface.co/{model_id}\n"
                        f"2. Нажмите 'Agree and access repository'\n"
                        f"3. Также примите условия для pyannote/segmentation-3.0\n"
                        f"4. Убедитесь, что токен имеет права 'read'"
                    )
                    continue
                else:
                    logger.warning(f"Ошибка при загрузке {model_id}: {e}")
                    continue
        else:
            # Если все попытки не удались
            if last_error:
                error_str = str(last_error)
                if "403" in error_str or "gated" in error_str.lower():
                    raise DiarizationError(
                        f"Нет доступа к моделям диаризации. "
                        f"Примите условия использования на HuggingFace:\n"
                        f"- https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                        f"- https://huggingface.co/pyannote/segmentation-3.0\n"
                        f"- https://huggingface.co/pyannote/speaker-diarization\n"
                        f"\nПосле принятия условий повторите попытку.",
                        cause=last_error
                    )
                else:
                    raise DiarizationError(
                        "Не удалось загрузить модель диаризации",
                        cause=last_error
                    )
            else:
                raise DiarizationError("Не удалось загрузить модель диаризации")
        
        # Проверяем, что pipeline загрузился
        if pipeline is None:
            raise DiarizationError(
                "Не удалось загрузить модель диаризации. "
                "Проверьте:\n"
                "1. HF_TOKEN действителен\n"
                "2. Приняты условия использования на HuggingFace:\n"
                "   - https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                "   - https://huggingface.co/pyannote/segmentation-3.0\n"
                "3. Токен имеет права 'read'"
            )
        
        # Перемещение на устройство
        device = torch.device(self.device)
        pipeline = pipeline.to(device)
        
        return pipeline
    
    def diarize(
        self,
        audio_path: Path | str,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> List[SpeakerSegment]:
        """
        Выполнить диаризацию аудио файла.
        
        Args:
            audio_path: Путь к аудио файлу (должен быть WAV, 16kHz, mono)
            num_speakers: Точное количество спикеров (если известно)
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
            
        Returns:
            Список сегментов с информацией о спикерах
        """
        audio_path = Path(audio_path)
        
        # Использование параметров по умолчанию
        min_speakers = min_speakers or self.min_speakers
        max_speakers = max_speakers or self.max_speakers
        
        try:
            # Подготовка параметров
            kwargs = {}
            if num_speakers is not None:
                kwargs["num_speakers"] = num_speakers
            else:
                if min_speakers is not None:
                    kwargs["min_speakers"] = min_speakers
                if max_speakers is not None:
                    kwargs["max_speakers"] = max_speakers
            
            # Запуск диаризации
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                diarization = self.pipeline(str(audio_path), **kwargs)
            
            # Преобразование результатов
            segments = []
            if hasattr(diarization, 'itertracks'):
                # pyannote.audio 3.x API (Annotation object)
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    segments.append(SpeakerSegment(
                        start=turn.start,
                        end=turn.end,
                        speaker=speaker
                    ))
            elif hasattr(diarization, 'speaker_diarization'):
                # pyannote.audio 4.0+ API
                for turn, speaker in diarization.speaker_diarization:
                    segments.append(SpeakerSegment(
                        start=turn.start,
                        end=turn.end,
                        speaker=speaker
                    ))
            else:
                logger.warning("Неизвестный формат результата диаризации: %s", type(diarization))
            
            # Сортировка по времени
            segments.sort(key=lambda s: s.start)
            
            # Переименование спикеров в человекочитаемый формат
            segments = self._rename_speakers(segments)
            
            return segments
            
        except Exception as e:
            raise DiarizationError(f"Ошибка при диаризации: {e}", cause=e)
    
    def _rename_speakers(
        self, 
        segments: List[SpeakerSegment]
    ) -> List[SpeakerSegment]:
        """
        Переименование спикеров в человекочитаемый формат.
        
        SPEAKER_00 -> Спикер №1
        SPEAKER_01 -> Спикер №2
        """
        # Получаем уникальных спикеров в порядке первого появления
        seen = set()
        speaker_order = []
        for seg in segments:
            if seg.speaker not in seen:
                seen.add(seg.speaker)
                speaker_order.append(seg.speaker)
        
        # Создаём маппинг
        speaker_map = {
            old_name: f"Спикер №{i+1}" 
            for i, old_name in enumerate(speaker_order)
        }
        
        # Применяем переименование
        for seg in segments:
            seg.speaker = speaker_map.get(seg.speaker, seg.speaker)
        
        return segments
    
    def map_speakers_to_transcription(
        self,
        transcription_segments: List[TranscriptionSegment],
        speaker_segments: List[SpeakerSegment],
    ) -> List[TranscriptionSegment]:
        """
        Сопоставление транскрипции с диаризацией по временным меткам.
        
        Для каждого сегмента транскрипции определяется спикер
        на основе временного пересечения с сегментами диаризации.
        
        Args:
            transcription_segments: Сегменты транскрипции
            speaker_segments: Сегменты диаризации
            
        Returns:
            Сегменты транскрипции с присвоенными спикерами
        """
        for trans_seg in transcription_segments:
            # Находим midpoint сегмента транскрипции
            midpoint = (trans_seg.start + trans_seg.end) / 2
            
            # Ищем спикера, говорившего в этот момент
            speaker = self._find_speaker_at_time(midpoint, speaker_segments)
            
            # Если не нашли по midpoint, ищем по максимальному пересечению
            if speaker is None:
                speaker = self._find_speaker_by_overlap(trans_seg, speaker_segments)
            
            trans_seg.speaker = speaker
        
        return transcription_segments
    
    def _find_speaker_at_time(
        self,
        time: float,
        speaker_segments: List[SpeakerSegment],
    ) -> Optional[str]:
        """Найти спикера, говорившего в указанный момент времени."""
        for seg in speaker_segments:
            if seg.start <= time <= seg.end:
                return seg.speaker
        return None
    
    def _find_speaker_by_overlap(
        self,
        trans_seg: TranscriptionSegment,
        speaker_segments: List[SpeakerSegment],
    ) -> Optional[str]:
        """
        Найти спикера с максимальным пересечением по времени.
        """
        max_overlap = 0
        best_speaker = None
        
        for sp_seg in speaker_segments:
            # Вычисляем пересечение
            overlap_start = max(trans_seg.start, sp_seg.start)
            overlap_end = min(trans_seg.end, sp_seg.end)
            overlap = max(0, overlap_end - overlap_start)
            
            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = sp_seg.speaker
        
        return best_speaker


class HybridDiarization:
    """
    Гибридная диаризация: VAD + эмбеддинги + кластеризация.
    
    Этот подход легче и быстрее полной pyannote диаризации,
    но может быть менее точным.
    """
    
    def __init__(
        self,
        hf_token: Optional[str] = None,
        device: str = "auto",
        num_clusters: Optional[int] = None,
    ):
        """
        Инициализация гибридной диаризации.
        
        Args:
            hf_token: HuggingFace токен
            device: Устройство
            num_clusters: Ожидаемое количество спикеров
        """
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.num_clusters = num_clusters
        
        self._embedding_model = None
        self._vad_model = None
    
    def _get_embedding_model(self):
        """Загрузка модели эмбеддингов спикера."""
        if self._embedding_model is None:
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except ImportError:
                raise DiarizationError(
                    "speechbrain не установлен. "
                    "Установите: pip install speechbrain"
                )
            
            self._embedding_model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir="pretrained_models/spkrec-ecapa-voxceleb",
                run_opts={"device": self.device}
            )
        
        return self._embedding_model
    
    def diarize(
        self,
        audio_path: Path | str,
        speech_segments: List[Tuple[float, float]],
        num_speakers: Optional[int] = None,
    ) -> List[SpeakerSegment]:
        """
        Гибридная диаризация.
        
        Args:
            audio_path: Путь к аудио
            speech_segments: Сегменты речи от VAD [(start, end), ...]
            num_speakers: Количество спикеров
            
        Returns:
            Сегменты с метками спикеров
        """
        try:
            import numpy as np
            from sklearn.cluster import AgglomerativeClustering
            import torchaudio
        except ImportError as e:
            raise DiarizationError(
                "Не установлены зависимости для гибридной диаризации. "
                "Установите: pip install scikit-learn torchaudio",
                cause=e
            )
        
        num_speakers = num_speakers or self.num_clusters or 2
        
        # Загружаем аудио
        waveform, sr = torchaudio.load(str(audio_path))
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        
        # Ресэмплинг если нужно
        target_sr = 16000
        if sr != target_sr:
            resampler = torchaudio.transforms.Resample(sr, target_sr)
            waveform = resampler(waveform)
        
        # Получаем эмбеддинги для каждого сегмента
        embeddings = []
        model = self._get_embedding_model()
        
        for start, end in speech_segments:
            start_sample = int(start * target_sr)
            end_sample = int(end * target_sr)
            segment = waveform[:, start_sample:end_sample]
            
            if segment.shape[1] < target_sr * 0.5:  # Минимум 0.5 сек
                continue
            
            embedding = model.encode_batch(segment)
            embeddings.append(embedding.squeeze().cpu().numpy())
        
        if len(embeddings) < 2:
            # Недостаточно сегментов для кластеризации
            return [
                SpeakerSegment(start=s, end=e, speaker="Спикер №1")
                for s, e in speech_segments
            ]
        
        # Кластеризация
        embeddings = np.array(embeddings)
        n_clusters = min(num_speakers, len(embeddings))
        
        clustering = AgglomerativeClustering(
            n_clusters=n_clusters,
            metric='cosine',
            linkage='average'
        )
        labels = clustering.fit_predict(embeddings)
        
        # Создаём результат
        result = []
        for (start, end), label in zip(speech_segments, labels):
            result.append(SpeakerSegment(
                start=start,
                end=end,
                speaker=f"Спикер №{label + 1}"
            ))
        
        return result


def get_diarization_manager(
    hf_token: Optional[str] = None,
    device: str = "auto",
    **kwargs
) -> DiarizationManager:
    """
    Получить менеджер диаризации.
    
    Args:
        hf_token: HuggingFace токен
        device: Устройство
        **kwargs: Дополнительные параметры
        
    Returns:
        Экземпляр DiarizationManager
    """
    return DiarizationManager(
        hf_token=hf_token,
        device=device,
        **kwargs
    )
