"""CQ-L2: setup_logging is idempotent — no duplicate handlers, even under concurrency."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from gigaam_transcriber.utils import setup_logging


@pytest.fixture(autouse=True)
def clean_llm_logger():
    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    handlers_before = list(llm_logger.handlers)
    yield
    for h in list(llm_logger.handlers):
        if h not in handlers_before:
            llm_logger.removeHandler(h)
            h.close()


def _rotating_handlers() -> list[RotatingFileHandler]:
    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    return [h for h in llm_logger.handlers if isinstance(h, RotatingFileHandler)]


def test_repeated_calls_same_file_single_handler(tmp_path):
    log_file = tmp_path / "llm.log"
    for _ in range(5):
        setup_logging(log_file=log_file)
    handlers = _rotating_handlers()
    assert len(handlers) == 1


def test_switching_files_replaces_handler(tmp_path):
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    setup_logging(log_file=first)
    setup_logging(log_file=second)
    handlers = _rotating_handlers()
    assert {Path(h.baseFilename) for h in handlers} == {first.resolve(), second.resolve()}


def test_concurrent_setup_logging_single_handler(tmp_path):
    log_file = tmp_path / "llm.log"
    barrier = threading.Barrier(8)

    def call_setup():
        barrier.wait()
        setup_logging(log_file=log_file)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(call_setup, range(8)))

    handlers = _rotating_handlers()
    assert len(handlers) == 1
    assert Path(handlers[0].baseFilename) == log_file.resolve()


def test_handler_still_writable_after_idempotent_calls(tmp_path):
    log_file = tmp_path / "llm.log"
    setup_logging(log_file=log_file)
    setup_logging(log_file=log_file)
    llm_logger = logging.getLogger("gigaam_transcriber.llm")
    llm_logger.info("still works")
    handler = _rotating_handlers()[0]
    handler.flush()
    assert "still works" in log_file.read_text(encoding="utf-8")
