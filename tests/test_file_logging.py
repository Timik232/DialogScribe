"""Tests for file-based LLM logging with rotation."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from gigaam_transcriber.utils import setup_logging


@pytest.fixture(autouse=True)
def _cleanup_llm_logger():
    """Remove handlers from gigaam_transcriber.llm after each test."""
    yield
    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    llm_logger.handlers.clear()
    llm_logger.setLevel(logging.NOTSET)


def test_setup_logging_creates_handler(tmp_path):
    """RotatingFileHandler added to gigaam_transcriber.llm logger."""
    log_file = str(tmp_path / "test_llm.log")
    setup_logging(log_file=log_file)

    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    handlers = [h for h in llm_logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1


def test_log_rotation_config(tmp_path):
    """RotatingFileHandler has maxBytes=50MB, backupCount=3."""
    log_file = str(tmp_path / "test_llm.log")
    setup_logging(log_file=log_file)

    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    handler = next(
        h for h in llm_logger.handlers if isinstance(h, RotatingFileHandler)
    )
    assert handler.maxBytes == 50 * 1024 * 1024  # 50MB
    assert handler.backupCount == 3


def test_env_var_config(tmp_path, monkeypatch):
    """LLM_LOG_FILE env var sets the log file path."""
    log_file = str(tmp_path / "env_var_llm.log")
    monkeypatch.setenv("LLM_LOG_FILE", log_file)
    # Pass explicit log_file=None to force env var lookup
    setup_logging(log_file=None)

    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    handler = next(
        h for h in llm_logger.handlers if isinstance(h, RotatingFileHandler)
    )
    assert Path(handler.baseFilename) == Path(log_file)


def test_default_path(tmp_path, monkeypatch):
    """Default path is /var/log/dialogscribe/llm.log when no env var."""
    monkeypatch.delenv("LLM_LOG_FILE", raising=False)
    # Use tmp_path to avoid permission errors on /var/log
    default_dir = tmp_path / "var" / "log" / "dialogscribe"
    monkeypatch.setenv("LLM_LOG_FILE", str(default_dir / "llm.log"))
    setup_logging(log_file=None)

    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    handler = next(
        h for h in llm_logger.handlers if isinstance(h, RotatingFileHandler)
    )
    assert handler.baseFilename.endswith("llm.log")


def test_log_directory_creation(tmp_path):
    """Parent directory created if missing."""
    nested_dir = tmp_path / "nested" / "log" / "dir"
    log_file = str(nested_dir / "test_llm.log")
    assert not nested_dir.exists()

    setup_logging(log_file=log_file)

    assert nested_dir.exists()
    assert nested_dir.is_dir()


def test_log_format(tmp_path):
    """Log entries contain expected format fields."""
    log_file = str(tmp_path / "test_llm.log")
    setup_logging(log_file=log_file)

    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    handler = next(
        h for h in llm_logger.handlers if isinstance(h, RotatingFileHandler)
    )
    # Flush any buffered content and close to ensure file is written
    handler.flush()

    # Write a test log message
    llm_logger.info("test message for format check")
    handler.flush()
    handler.close()

    content = Path(log_file).read_text(encoding="utf-8")
    assert "INFO" in content
    assert "gigaam_transcriber.llm" in content
    assert "test message for format check" in content


def test_llm_logger_level_is_info(tmp_path):
    """LLM logger is set to INFO level."""
    log_file = str(tmp_path / "test_llm.log")
    setup_logging(log_file=log_file)

    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    assert llm_logger.level == logging.INFO
