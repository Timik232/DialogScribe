"""Entrypoint secrets shim: JSON validation, stale shell-format detection.

Covers: valid JSON renders allowlisted key=value lines (shell-quoted),
non-allowlisted keys skipped, stale shell-format file fails closed with the
specific remediation message (values never echoed), garbage keeps the generic
invalid-JSON error, and the pre-existing fail-closed paths (missing file,
empty object, non-object JSON).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "entrypoint_secrets.py"


def run_shim(secrets_path: Path | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["VAULT_SECRETS_FILE"] = str(secrets_path) if secrets_path else "/nonexistent/secrets.json"
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class TestValidJson:
    def test_allowlisted_keys_rendered_shell_quoted(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text('{"JWT_SECRET": "abc123", "DATABASE_URL": "pg://x"}', encoding="utf-8")
        result = run_shim(secrets)
        assert result.returncode == 0
        assert result.stdout == "JWT_SECRET='abc123'\nDATABASE_URL='pg://x'\n"
        assert result.stderr == ""

    def test_non_allowlisted_keys_skipped(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text('{"EVIL_KEY": "x", "API_KEY": "y"}', encoding="utf-8")
        result = run_shim(secrets)
        assert result.returncode == 0
        assert result.stdout == "API_KEY='y'\n"


class TestStaleShellFormat:
    def test_export_style_file_fails_with_specific_message(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text(
            "export JWT_SECRET=super-secret-value\nDATABASE_URL='sqlite:///prod.db'\n",
            encoding="utf-8",
        )
        result = run_shim(secrets)
        assert result.returncode == 1
        assert "stale shell-format secrets file" in result.stderr
        assert "Vault template must render JSON" in result.stderr
        assert "super-secret-value" not in result.stderr
        assert "super-secret-value" not in result.stdout

    def test_bare_assignment_file_fails_with_specific_message(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text("API_KEY=leaky\n", encoding="utf-8")
        result = run_shim(secrets)
        assert result.returncode == 1
        assert "stale shell-format secrets file" in result.stderr
        assert "leaky" not in result.stderr


class TestFailClosed:
    def test_garbage_keeps_generic_invalid_json_error(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text("\x00\x01{{{ not json <<<>>>", encoding="utf-8", errors="replace")
        result = run_shim(secrets)
        assert result.returncode == 1
        assert "stale shell-format" not in result.stderr
        assert "not valid JSON" in result.stderr

    def test_missing_file_fails_closed(self, tmp_path):
        result = run_shim(tmp_path / "absent.json")
        assert result.returncode == 1
        assert "missing" in result.stderr

    def test_empty_object_fails_closed(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text("{}", encoding="utf-8")
        result = run_shim(secrets)
        assert result.returncode == 1
        assert "empty" in result.stderr

    def test_non_object_json_fails_closed(self, tmp_path):
        secrets = tmp_path / "secrets.json"
        secrets.write_text("[1, 2, 3]", encoding="utf-8")
        result = run_shim(secrets)
        assert result.returncode == 1
        assert "must be an object" in result.stderr
