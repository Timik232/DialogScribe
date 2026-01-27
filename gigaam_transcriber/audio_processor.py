"""
Модуль обработки аудио для GigaAM Transcriber.

Обеспечивает:
- Конвертацию аудио в формат, оптимальный для GigaAM (16kHz, mono, PCM)
- Извлечение аудио из видео файлов
- Получение информации о медиафайлах
- Разбиение аудио по тишине
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .exceptions import (
    AudioProcessingError,
    FFmpegNotFoundError,
    UnsupportedFormatError,
)

logger = logging.getLogger(__name__)


class AudioProcessor:
    """Обработка аудио для GigaAM."""
    
    # Параметры для GigaAM
    SAMPLE_RATE: int = 16000
    CHANNELS: int = 1  # Mono
    BIT_DEPTH: int = 16
    
    # Поддерживаемые форматы
    AUDIO_FORMATS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus'}
    VIDEO_FORMATS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv', '.flv', '.mpeg', '.mpg'}
    
    def __init__(self, ffmpeg_path: Optional[str] = None):
        """
        Инициализация процессора.
        
        Args:
            ffmpeg_path: Путь к ffmpeg. Если не указан, ищется в PATH.
        """
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()
        self._ffprobe_path = self._find_ffprobe()
    
    def _find_ffmpeg(self) -> str:
        """Поиск ffmpeg в системе."""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise FFmpegNotFoundError()
        return ffmpeg
    
    def _find_ffprobe(self) -> Optional[str]:
        """Поиск ffprobe в системе."""
        return shutil.which("ffprobe")
    
    @classmethod
    def is_audio_file(cls, path: Path | str) -> bool:
        """Проверка, является ли файл аудио."""
        return Path(path).suffix.lower() in cls.AUDIO_FORMATS
    
    @classmethod
    def is_video_file(cls, path: Path | str) -> bool:
        """Проверка, является ли файл видео."""
        return Path(path).suffix.lower() in cls.VIDEO_FORMATS
    
    @classmethod
    def is_supported_file(cls, path: Path | str) -> bool:
        """Проверка, поддерживается ли формат файла."""
        return cls.is_audio_file(path) or cls.is_video_file(path)
    
    def get_duration(self, path: Path | str) -> float:
        """
        Получить длительность медиафайла в секундах.
        
        Args:
            path: Путь к файлу
            
        Returns:
            Длительность в секундах
        """
        path = Path(path)
        
        if not self._ffprobe_path:
            # Fallback: загрузить аудио и посчитать
            return self._get_duration_fallback(path)
        
        try:
            result = subprocess.run(
                [
                    self._ffprobe_path,
                    "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "json",
                    str(path)
                ],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
            logger.warning(f"ffprobe failed, using fallback: {e}")
            return self._get_duration_fallback(path)
    
    def _get_duration_fallback(self, path: Path) -> float:
        """Получение длительности через загрузку аудио."""
        try:
            import torchaudio
            info = torchaudio.info(str(path))
            return info.num_frames / info.sample_rate
        except Exception as e:
            raise AudioProcessingError(
                f"Не удалось определить длительность файла",
                file_path=str(path),
                cause=e
            )
    
    def get_media_info(self, path: Path | str) -> dict:
        """
        Получить информацию о медиафайле.
        
        Returns:
            Словарь с информацией: duration, sample_rate, channels, codec и т.д.
        """
        path = Path(path)
        
        if not self._ffprobe_path:
            return {"duration": self._get_duration_fallback(path)}
        
        try:
            result = subprocess.run(
                [
                    self._ffprobe_path,
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    str(path)
                ],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(result.stdout)
            
            info = {
                "duration": float(data["format"].get("duration", 0)),
                "format": data["format"].get("format_name", "unknown"),
                "size_bytes": int(data["format"].get("size", 0)),
            }
            
            # Поиск аудио потока
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    info["sample_rate"] = int(stream.get("sample_rate", 0))
                    info["channels"] = int(stream.get("channels", 0))
                    info["codec"] = stream.get("codec_name", "unknown")
                    info["bit_rate"] = int(stream.get("bit_rate", 0))
                    break
            
            return info
        except Exception as e:
            logger.warning(f"ffprobe failed: {e}")
            return {"duration": self._get_duration_fallback(path)}
    
    def normalize(
        self,
        input_path: Path | str,
        output_path: Optional[Path | str] = None,
        sample_rate: int = None,
        channels: int = None,
    ) -> Path:
        """
        Нормализация аудио в формат, оптимальный для GigaAM.
        
        Конвертирует в: 16kHz, mono, 16-bit PCM WAV
        
        Args:
            input_path: Входной файл
            output_path: Выходной файл (если None, создаётся временный)
            sample_rate: Частота дискретизации (по умолчанию 16000)
            channels: Количество каналов (по умолчанию 1 - mono)
            
        Returns:
            Путь к нормализованному файлу
        """
        input_path = Path(input_path)
        sample_rate = sample_rate or self.SAMPLE_RATE
        channels = channels or self.CHANNELS
        
        # Проверка формата
        if not self.is_supported_file(input_path):
            raise UnsupportedFormatError(input_path.suffix)
        
        # Определение выходного пути
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
        output_path = Path(output_path)
        
        # Команда ffmpeg
        cmd = [
            self.ffmpeg_path,
            "-y",  # Перезаписывать
            "-i", str(input_path),
            "-ar", str(sample_rate),  # Sample rate
            "-ac", str(channels),  # Channels (mono)
            "-c:a", "pcm_s16le",  # 16-bit PCM
            "-vn",  # Без видео
            str(output_path)
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logger.debug(f"Audio normalized: {input_path} -> {output_path}")
            return output_path
        except subprocess.CalledProcessError as e:
            raise AudioProcessingError(
                f"FFmpeg ошибка: {e.stderr}",
                file_path=str(input_path),
                cause=e
            )
    
    def extract_audio_from_video(
        self,
        video_path: Path | str,
        output_path: Optional[Path | str] = None,
        normalize: bool = True,
    ) -> Path:
        """
        Извлечение аудио из видео файла.
        
        Args:
            video_path: Путь к видео
            output_path: Путь для сохранения аудио (если None, создаётся временный)
            normalize: Нормализовать аудио для GigaAM (16kHz, mono)
            
        Returns:
            Путь к извлечённому аудио файлу
        """
        video_path = Path(video_path)
        
        if not self.is_video_file(video_path):
            raise UnsupportedFormatError(video_path.suffix)
        
        # Определение выходного пути
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
        output_path = Path(output_path)
        
        if normalize:
            # Извлечение с нормализацией
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i", str(video_path),
                "-vn",  # Без видео
                "-ar", str(self.SAMPLE_RATE),
                "-ac", str(self.CHANNELS),
                "-c:a", "pcm_s16le",
                str(output_path)
            ]
        else:
            # Просто извлечение
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i", str(video_path),
                "-vn",
                "-c:a", "pcm_s16le",
                str(output_path)
            ]
        
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.debug(f"Audio extracted: {video_path} -> {output_path}")
            return output_path
        except subprocess.CalledProcessError as e:
            raise AudioProcessingError(
                f"Ошибка извлечения аудио: {e.stderr}",
                file_path=str(video_path),
                cause=e
            )
    
    def prepare_for_gigaam(
        self,
        input_path: Path | str,
        output_path: Optional[Path | str] = None,
    ) -> Path:
        """
        Подготовка файла для GigaAM.
        
        Автоматически определяет тип файла (audio/video) и
        конвертирует в формат, оптимальный для GigaAM.
        
        Args:
            input_path: Входной файл (аудио или видео)
            output_path: Выходной файл (опционально)
            
        Returns:
            Путь к подготовленному WAV файлу
        """
        input_path = Path(input_path)
        
        if self.is_video_file(input_path):
            return self.extract_audio_from_video(input_path, output_path, normalize=True)
        elif self.is_audio_file(input_path):
            # Проверяем, нужна ли конвертация
            if input_path.suffix.lower() == ".wav":
                info = self.get_media_info(input_path)
                if (info.get("sample_rate") == self.SAMPLE_RATE and 
                    info.get("channels") == self.CHANNELS):
                    # Файл уже в нужном формате
                    if output_path:
                        shutil.copy(input_path, output_path)
                        return Path(output_path)
                    return input_path
            return self.normalize(input_path, output_path)
        else:
            raise UnsupportedFormatError(input_path.suffix)
    
    def split_by_silence(
        self,
        audio_path: Path | str,
        min_silence_len: int = 500,  # мс
        silence_thresh: int = -40,  # дБ
        keep_silence: int = 200,  # мс
    ) -> List[Tuple[float, float]]:
        """
        Разбиение аудио по паузам (тишине).
        
        Args:
            audio_path: Путь к аудио файлу
            min_silence_len: Минимальная длина тишины для разбиения (мс)
            silence_thresh: Порог тишины в дБ
            keep_silence: Сколько тишины оставлять в начале/конце сегментов (мс)
            
        Returns:
            Список кортежей (start, end) в секундах
        """
        try:
            from pydub import AudioSegment
            from pydub.silence import detect_nonsilent
        except ImportError:
            logger.warning("pydub не установлен, используем простое разбиение")
            duration = self.get_duration(audio_path)
            return [(0, duration)]
        
        audio = AudioSegment.from_file(str(audio_path))
        
        # Определение не-тихих сегментов
        nonsilent_ranges = detect_nonsilent(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
            seek_step=10
        )
        
        if not nonsilent_ranges:
            return []
        
        # Конвертация в секунды с добавлением padding
        segments = []
        for start_ms, end_ms in nonsilent_ranges:
            start_sec = max(0, (start_ms - keep_silence)) / 1000.0
            end_sec = min(len(audio), (end_ms + keep_silence)) / 1000.0
            segments.append((start_sec, end_sec))
        
        return segments
    
    def load_audio_segment(
        self,
        audio_path: Path | str,
        start: float,
        end: float,
    ) -> np.ndarray:
        """
        Загрузка сегмента аудио.
        
        Args:
            audio_path: Путь к аудио файлу
            start: Начало сегмента (секунды)
            end: Конец сегмента (секунды)
            
        Returns:
            NumPy массив с аудио данными
        """
        try:
            import torchaudio
        except ImportError:
            raise AudioProcessingError(
                "torchaudio не установлен. Установите: pip install torchaudio"
            )
        
        waveform, sr = torchaudio.load(str(audio_path))
        
        # Конвертация в нужный sample rate если необходимо
        if sr != self.SAMPLE_RATE:
            resampler = torchaudio.transforms.Resample(sr, self.SAMPLE_RATE)
            waveform = resampler(waveform)
        
        # Конвертация в mono
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        
        # Извлечение сегмента
        start_sample = int(start * self.SAMPLE_RATE)
        end_sample = int(end * self.SAMPLE_RATE)
        
        segment = waveform[0, start_sample:end_sample]
        
        return segment.numpy()


# Глобальный экземпляр для удобства
_processor: Optional[AudioProcessor] = None


def get_audio_processor() -> AudioProcessor:
    """Получить глобальный экземпляр AudioProcessor."""
    global _processor
    if _processor is None:
        _processor = AudioProcessor()
    return _processor
