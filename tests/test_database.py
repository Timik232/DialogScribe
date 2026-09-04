import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gigaam_transcriber.database import _ensure_sqlite_parent_dir


class TestEnsureSqliteParentDir:
    def test_creates_missing_nested_parent(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "dialogscribe.db"
        _ensure_sqlite_parent_dir(f"sqlite+aiosqlite:///{target}")
        assert target.parent.is_dir()

    def test_relative_path_creates_dir_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _ensure_sqlite_parent_dir("sqlite+aiosqlite:///./data/dialogscribe.db")
        assert (tmp_path / "data").is_dir()

    def test_absolute_path_supported(self, tmp_path):
        target = tmp_path / "abs" / "x.db"
        _ensure_sqlite_parent_dir(f"sqlite+aiosqlite:////{str(target).lstrip('/')}")
        assert target.parent.is_dir()

    def test_memory_url_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _ensure_sqlite_parent_dir("sqlite+aiosqlite:///:memory:")
        assert list(tmp_path.iterdir()) == []

    def test_query_params_stripped(self, tmp_path):
        target = tmp_path / "q" / "x.db"
        _ensure_sqlite_parent_dir(f"sqlite+aiosqlite:///{target}?timeout=30")
        assert target.parent.is_dir()

    def test_non_sqlite_url_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _ensure_sqlite_parent_dir("postgresql+asyncpg://user:pass@localhost:5432/db")
        assert list(tmp_path.iterdir()) == []
