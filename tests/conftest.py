"""
Конфигурация pytest и фикстуры для тестов.
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# Добавляем путь к пакету
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Директория с тестовыми данными."""
    return Path(__file__).parent / "test_data"


@pytest.fixture(scope="function")
def temp_dir() -> Generator[Path, None, None]:
    """Временная директория для тестов."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="session")
def has_gpu() -> bool:
    """Проверка наличия GPU."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


@pytest.fixture(scope="session")
def has_hf_token() -> bool:
    """Проверка наличия HuggingFace токена."""
    return os.getenv("HF_TOKEN") is not None


@pytest.fixture(scope="session")
def hf_token() -> str | None:
    """HuggingFace токен."""
    return os.getenv("HF_TOKEN")


@pytest.fixture(scope="function")
def sample_transcription_segment():
    """Пример сегмента транскрипции."""
    from gigaam_transcriber import TranscriptionSegment
    
    return TranscriptionSegment(
        text="Привет, как дела?",
        start=0.0,
        end=2.5,
        speaker="Спикер №1",
    )


@pytest.fixture(scope="function")
def sample_transcription_result(sample_transcription_segment):
    """Пример результата транскрипции."""
    from gigaam_transcriber import TranscriptionResult, TranscriptionSegment
    
    segments = [
        sample_transcription_segment,
        TranscriptionSegment(
            text="Отлично, спасибо!",
            start=2.5,
            end=4.0,
            speaker="Спикер №2",
        ),
        TranscriptionSegment(
            text="А у тебя как?",
            start=4.0,
            end=5.5,
            speaker="Спикер №2",
        ),
    ]
    
    return TranscriptionResult(
        text="Привет, как дела? Отлично, спасибо! А у тебя как?",
        segments=segments,
        duration=5.5,
        language="ru",
        model_name="v3_e2e_rnnt",
        processing_time=1.5,
        metadata={"source": "test.wav"},
    )


def pytest_configure(config):
    """Конфигурация маркеров pytest."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow"
    )
    config.addinivalue_line(
        "markers", "requires_gpu: marks tests that require GPU"
    )
    config.addinivalue_line(
        "markers", "requires_hf_token: marks tests that require HuggingFace token"
    )
    config.addinivalue_line(
        "markers", "requires_model: marks tests that require GigaAM model"
    )
