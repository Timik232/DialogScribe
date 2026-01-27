"""
Вспомогательные утилиты для GigaAM Transcriber.
"""

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def format_time(seconds: float, style: str = "auto") -> str:
    """
    Форматирование времени в строку.
    
    Args:
        seconds: Время в секундах
        style: Стиль форматирования:
            - "auto": Автоматический выбор (HH:MM:SS:cc или MM:SS:cc)
            - "full": Всегда HH:MM:SS:cc
            - "short": MM:SS
            - "srt": HH:MM:SS,mmm (для SRT субтитров)
            - "vtt": HH:MM:SS.mmm (для WebVTT)
            
    Returns:
        Отформатированная строка времени
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    full_secs = int(secs)
    
    if style == "srt":
        millis = int((secs - full_secs) * 1000)
        return f"{hours:02d}:{minutes:02d}:{full_secs:02d},{millis:03d}"
    
    elif style == "vtt":
        millis = int((secs - full_secs) * 1000)
        return f"{hours:02d}:{minutes:02d}:{full_secs:02d}.{millis:03d}"
    
    elif style == "short":
        if hours > 0:
            return f"{hours}:{minutes:02d}:{full_secs:02d}"
        return f"{minutes:02d}:{full_secs:02d}"
    
    elif style == "full":
        centis = int((secs - full_secs) * 100)
        return f"{hours:02d}:{minutes:02d}:{full_secs:02d}:{centis:02d}"
    
    else:  # auto
        centis = int((secs - full_secs) * 100)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{full_secs:02d}:{centis:02d}"
        return f"{minutes:02d}:{full_secs:02d}:{centis:02d}"


def parse_time(time_str: str) -> float:
    """
    Парсинг строки времени в секунды.
    
    Поддерживаемые форматы:
        - HH:MM:SS.mmm
        - HH:MM:SS,mmm
        - HH:MM:SS:cc
        - MM:SS.mmm
        - MM:SS
        - SS.mmm
        - SS
        
    Args:
        time_str: Строка времени
        
    Returns:
        Время в секундах
    """
    # Нормализуем разделители
    time_str = time_str.replace(",", ".").replace(":", " ").strip()
    parts = time_str.split()
    
    if len(parts) == 1:
        # Только секунды (возможно с миллисекундами)
        return float(parts[0])
    
    elif len(parts) == 2:
        # MM:SS или SS:cc
        minutes_or_secs = float(parts[0])
        secs_or_centis = float(parts[1])
        
        if minutes_or_secs >= 60:
            # Это минуты и секунды
            return minutes_or_secs * 60 + secs_or_centis
        elif secs_or_centis >= 60:
            # Это секунды и сотые
            return minutes_or_secs + secs_or_centis / 100
        else:
            # Скорее всего минуты:секунды
            return minutes_or_secs * 60 + secs_or_centis
    
    elif len(parts) == 3:
        # HH:MM:SS
        hours = float(parts[0])
        minutes = float(parts[1])
        secs = float(parts[2])
        return hours * 3600 + minutes * 60 + secs
    
    elif len(parts) == 4:
        # HH:MM:SS:cc
        hours = float(parts[0])
        minutes = float(parts[1])
        secs = float(parts[2])
        centis = float(parts[3])
        return hours * 3600 + minutes * 60 + secs + centis / 100
    
    raise ValueError(f"Неверный формат времени: {time_str}")


def get_file_hash(file_path: Path, algorithm: str = "md5") -> str:
    """
    Вычисление хэша файла.
    
    Args:
        file_path: Путь к файлу
        algorithm: Алгоритм хэширования (md5, sha256, etc.)
        
    Returns:
        Хэш файла в виде hex строки
    """
    hasher = hashlib.new(algorithm)
    
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    
    return hasher.hexdigest()


def sanitize_filename(filename: str) -> str:
    """
    Очистка имени файла от недопустимых символов.
    
    Args:
        filename: Исходное имя файла
        
    Returns:
        Безопасное имя файла
    """
    # Удаляем/заменяем недопустимые символы
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Удаляем начальные/конечные пробелы и точки
    filename = filename.strip('. ')
    # Ограничиваем длину
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200 - len(ext)] + ext
    
    return filename or "unnamed"


def ensure_dir(path: Path | str) -> Path:
    """
    Создание директории, если она не существует.
    
    Args:
        path: Путь к директории
        
    Returns:
        Путь к директории
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_output_path(
    input_path: Path | str,
    output_dir: Optional[Path | str] = None,
    suffix: str = "",
    extension: str = ".txt",
) -> Path:
    """
    Генерация пути для выходного файла.
    
    Args:
        input_path: Путь к входному файлу
        output_dir: Директория для выходного файла (опционально)
        suffix: Суффикс для имени файла
        extension: Расширение выходного файла
        
    Returns:
        Путь к выходному файлу
    """
    input_path = Path(input_path)
    
    # Формируем имя файла
    stem = input_path.stem
    if suffix:
        stem = f"{stem}{suffix}"
    
    # Определяем директорию
    if output_dir:
        output_dir = Path(output_dir)
        ensure_dir(output_dir)
    else:
        output_dir = input_path.parent
    
    # Формируем полный путь
    output_path = output_dir / f"{stem}{extension}"
    
    return output_path


def format_duration_human(seconds: float) -> str:
    """
    Форматирование длительности в человекочитаемый формат.
    
    Примеры:
        - 45.3 -> "45 сек"
        - 125.7 -> "2 мин 5 сек"
        - 3725.2 -> "1 ч 2 мин 5 сек"
        
    Args:
        seconds: Длительность в секундах
        
    Returns:
        Человекочитаемая строка
    """
    if seconds < 60:
        return f"{int(seconds)} сек"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0:
        parts.append(f"{minutes} мин")
    if secs > 0 or not parts:
        parts.append(f"{secs} сек")
    
    return " ".join(parts)


def setup_logging(
    level: int = logging.INFO,
    format_str: Optional[str] = None,
    log_file: Optional[Path | str] = None,
) -> None:
    """
    Настройка логирования.
    
    Args:
        level: Уровень логирования
        format_str: Формат сообщений
        log_file: Путь к файлу лога (опционально)
    """
    if format_str is None:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    handlers = [logging.StreamHandler()]
    
    if log_file:
        log_file = Path(log_file)
        ensure_dir(log_file.parent)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    
    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=handlers,
    )


class ProgressTracker:
    """Простой трекер прогресса."""
    
    def __init__(self, total: int, description: str = "Processing"):
        """
        Инициализация.
        
        Args:
            total: Общее количество шагов
            description: Описание процесса
        """
        self.total = total
        self.current = 0
        self.description = description
    
    def update(self, n: int = 1) -> None:
        """Обновить прогресс."""
        self.current += n
        logger.info(f"{self.description}: {self.current}/{self.total}")
    
    def set(self, n: int) -> None:
        """Установить текущее значение."""
        self.current = n
        logger.info(f"{self.description}: {self.current}/{self.total}")
    
    @property
    def progress(self) -> float:
        """Прогресс в процентах."""
        if self.total == 0:
            return 0
        return (self.current / self.total) * 100
    
    @property
    def is_complete(self) -> bool:
        """Завершён ли процесс."""
        return self.current >= self.total
