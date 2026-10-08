"""Task 15: guarded subprocess execution and audio temp-resource cleanup."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gigaam_transcriber import audio_processor as ap
from gigaam_transcriber.audio_processor import AudioProcessor
from gigaam_transcriber.exceptions import AudioProcessingError


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except (FileNotFoundError, OSError):
        pass
    return True


def _wait_pid_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


class TestRunSubprocessGuard:
    def test_success_returns_completed_process(self):
        result = ap._run_subprocess(
            [sys.executable, "-c", "import sys; sys.stdout.write('hello'); sys.exit(0)"],
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout == b"hello"

    def test_nonzero_exit_raises_sanitized_error(self):
        with pytest.raises(AudioProcessingError) as excinfo:
            ap._run_subprocess(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.write('/tmp/secret/internal/path leak'); sys.exit(3)",
                ],
                timeout=10,
            )
        msg = str(excinfo.value)
        assert "exit code 3" in msg
        assert "/tmp/secret" not in msg
        assert "internal/path" not in msg
        assert "[path]" in msg

    def test_missing_binary_raises_audio_error(self):
        with pytest.raises(AudioProcessingError):
            ap._run_subprocess(["definitely-not-a-real-binary-xyz"], timeout=5)

    def test_timeout_kills_process_group_with_grandchildren(self, tmp_path):
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import os, time\n"
            f"open({str(tmp_path / 'grandchild.pid')!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(300)\n"
        )
        child_script = tmp_path / "child.py"
        child_script.write_text(
            "import os, subprocess, sys, time\n"
            f"open({str(tmp_path / 'child.pid')!r}, 'w').write(str(os.getpid()))\n"
            f"subprocess.Popen([sys.executable, {str(grandchild_script)!r}])\n"
            "time.sleep(300)\n"
        )
        with pytest.raises(AudioProcessingError) as excinfo:
            ap._run_subprocess([sys.executable, str(child_script)], timeout=1.5)
        assert "timeout" in str(excinfo.value).lower()
        assert "/tmp" not in str(excinfo.value)

        child_pid = int((tmp_path / "child.pid").read_text())
        grandchild_pid = int((tmp_path / "grandchild.pid").read_text())
        assert _wait_pid_gone(child_pid), "hung child survived the group kill"
        assert _wait_pid_gone(grandchild_pid), "grandchild survived the group kill"

    def test_output_size_cap_rejects_runaway_output(self):
        with pytest.raises(AudioProcessingError) as excinfo:
            ap._run_subprocess(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"],
                timeout=10,
                max_output_bytes=1024,
            )
        assert "output beyond the allowed limit" in str(excinfo.value)

    def test_check_false_returns_bad_returncode(self):
        result = ap._run_subprocess(
            [sys.executable, "-c", "import sys; sys.exit(7)"],
            timeout=10,
            check=False,
        )
        assert result.returncode == 7


class TestAudioProcessorPartialCleanup:
    def _record_mkstemp(self, tmp_path):
        created = []
        real_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return fd, path

        return created, tracking_mkstemp

    def test_normalize_failure_removes_owned_temp_output(self, tmp_path):
        created, tracking = self._record_mkstemp(tmp_path)
        processor = AudioProcessor()
        src = tmp_path / "input.wav"
        src.write_bytes(b"RIFF")

        with (
            patch.object(ap.tempfile, "mkstemp", tracking),
            patch.object(
                ap,
                "_run_subprocess",
                side_effect=AudioProcessingError("ffmpeg failed with exit code 1: boom"),
            ),
        ):
            with pytest.raises(AudioProcessingError):
                processor.normalize(src)
        assert created, "normalize should have created a temp output"
        assert not Path(created[0]).exists()

    def test_extract_audio_failure_removes_owned_temp_output(self, tmp_path):
        created, tracking = self._record_mkstemp(tmp_path)
        processor = AudioProcessor()
        src = tmp_path / "input.mp4"
        src.write_bytes(b"RIFF")

        with (
            patch.object(ap.tempfile, "mkstemp", tracking),
            patch.object(
                ap,
                "_run_subprocess",
                side_effect=AudioProcessingError("ffmpeg exceeded 5s timeout and was terminated"),
            ),
        ):
            with pytest.raises(AudioProcessingError):
                processor.extract_audio_from_video(src)
        assert created
        assert not Path(created[0]).exists()

    def test_split_audio_failure_removes_all_chunks_and_workspace(self, tmp_path):
        processor = AudioProcessor()
        workspaces = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            workspaces.append(path)
            return path

        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            out = Path(cmd[-1])
            out.write_bytes(b"PARTIAL")
            if calls["n"] >= 2:
                raise AudioProcessingError("ffmpeg exceeded 2s timeout and was terminated")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with (
            patch.object(processor, "get_duration", return_value=700.0),
            patch.object(ap.tempfile, "mkdtemp", tracking_mkdtemp),
            patch.object(ap, "_run_subprocess", side_effect=fake_run),
        ):
            with pytest.raises(AudioProcessingError):
                processor.split_audio(tmp_path / "a.wav", chunk_duration=300.0)

        assert calls["n"] == 2
        assert workspaces
        assert not Path(workspaces[0]).exists()

    def test_split_audio_success_returns_tracked_workspace_chunks(self, tmp_path):
        processor = AudioProcessor()
        workspaces = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            workspaces.append(path)
            return path

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"CK")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with (
            patch.object(processor, "get_duration", return_value=350.0),
            patch.object(ap.tempfile, "mkdtemp", tracking_mkdtemp),
            patch.object(ap, "_run_subprocess", side_effect=fake_run),
        ):
            chunks = processor.split_audio(tmp_path / "a.wav", chunk_duration=300.0)

        assert len(chunks) == 2
        workspace = Path(workspaces[0])
        assert all(p.parent == workspace for p, _, _ in chunks)
        assert all(p.exists() for p, _, _ in chunks)
        for p, _, _ in chunks:
            p.unlink()
        workspace.rmdir()

    def test_split_audio_cancellation_still_cleans(self, tmp_path):
        processor = AudioProcessor()
        workspaces = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            workspaces.append(path)
            return path

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"PARTIAL")
            if Path(cmd[-1]).name == "chunk-00001.wav":
                raise KeyboardInterrupt
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with (
            patch.object(processor, "get_duration", return_value=700.0),
            patch.object(ap.tempfile, "mkdtemp", tracking_mkdtemp),
            patch.object(ap, "_run_subprocess", side_effect=fake_run),
        ):
            with pytest.raises(KeyboardInterrupt):
                processor.split_audio(tmp_path / "a.wav", chunk_duration=300.0)

        assert workspaces
        assert not Path(workspaces[0]).exists()


class TestRealFfmpegGuard:
    def test_normalize_bad_input_raises_sanitized(self, tmp_path):
        processor = AudioProcessor()
        src = tmp_path / "input.mp3"
        src.write_bytes(b"not audio at all")
        with pytest.raises(AudioProcessingError) as excinfo:
            processor.normalize(src)
        assert str(tmp_path) not in str(excinfo.value)

    def test_normalize_real_audio_succeeds(self, tmp_path):
        pytest.importorskip("soundfile")
        import numpy as np
        import soundfile as sf

        src = tmp_path / "tone.wav"
        t = np.linspace(0, 0.5, 8000, endpoint=False)
        sf.write(str(src), (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000)

        processor = AudioProcessor()
        out = processor.normalize(src)
        try:
            assert out.exists()
            assert out.stat().st_size > 0
        finally:
            out.unlink(missing_ok=True)


class TestLiveHintsDecodeGuard:
    def _patched_decode(self, raw, run_side_effect):
        from gigaam_transcriber import live_hints_service as lh

        created = []
        real_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return fd, path

        with (
            patch.object(lh.tempfile, "mkstemp", tracking_mkstemp),
            patch.object(lh, "_run_subprocess", side_effect=run_side_effect),
        ):
            adapter = lh.AudioAdapter.__new__(lh.AudioAdapter)
            result = adapter._decode_to_wav(raw)
        return result, created

    def test_decode_success_reads_wav_bytes(self):
        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"WAVDATA")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        result, created = self._patched_decode(b"webm-bytes", fake_run)
        assert result == b"WAVDATA"
        assert all(not Path(p).exists() for p in created)

    def test_decode_nonzero_rc_returns_none(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, b"", b"cannot decode")

        result, created = self._patched_decode(b"garbage", fake_run)
        assert result is None
        assert all(not Path(p).exists() for p in created)

    def test_decode_timeout_propagates_and_cleans(self):
        from gigaam_transcriber import live_hints_service as lh

        created = []
        real_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return fd, path

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"PARTIAL")
            raise AudioProcessingError("ffmpeg exceeded 1s timeout and was terminated")

        with (
            patch.object(lh.tempfile, "mkstemp", tracking_mkstemp),
            patch.object(lh, "_run_subprocess", side_effect=fake_run),
        ):
            adapter = lh.AudioAdapter.__new__(lh.AudioAdapter)
            with pytest.raises(AudioProcessingError):
                adapter._decode_to_wav(b"webm-bytes")
        assert all(not Path(p).exists() for p in created)
