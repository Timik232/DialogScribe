import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gigaam_transcriber.database as database_module
from gigaam_transcriber.database import _default_database_url, _ensure_sqlite_parent_dir


class TestEnsureSqliteParentDir:
    def test_creates_missing_nested_parent(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "dialogscribe.db"
        _ensure_sqlite_parent_dir(f"sqlite+aiosqlite:///{target}")
        assert target.parent.is_dir()

    def test_relative_path_creates_dir_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _ensure_sqlite_parent_dir("sqlite+aiosqlite:///./scratch/dialogscribe.db")
        assert (tmp_path / "scratch").is_dir()

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


class TestDevDefaultUrl:
    """The fallback URL must be an unmistakably disposable CWD-relative file.

    Regression guard for the incident where the default pointed at
    ./data/dialogscribe.db and evidence tooling polluted a production-looking
    database. Uses the helper directly — reloading the database module here
    would rebind get_db/engine objects that later tests patch by identity.
    """

    def test_default_is_throwaway_and_not_under_data_dir(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        url = _default_database_url()
        assert url == "sqlite+aiosqlite:///./dialogscribe-dev.db"
        assert "/data/" not in url

    def test_explicit_database_url_env_is_passed_through(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
        assert _default_database_url() == "postgresql+asyncpg://u:p@h:5432/db"

    def test_module_constant_bound_to_new_default(self):
        assert database_module.DATABASE_URL == _default_database_url()
