"""Source-level tripwire tests for the uploader audio-source selection.

Feature (2026-11-15): AudioUploader on the transcription page records not
only from the microphone but also from device/tab audio (getDisplayMedia),
with a "mic + device" mode that mixes both tracks into one file via
AudioContext. Live mode (LiveAudioCapture.svelte) already had device-audio
toggles; these invariants pin the same capability on the transcription page.

The tests assert on the component SOURCE (string-level invariants) because
capture behaviour needs a real browser picker and hardware — jsdom/happy-dom
cannot provide getDisplayMedia. If one of these fails, someone changed the
capture path — re-verify recording of each source in a real browser before
"fixing" the test.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT = (
    REPO_ROOT / "frontend" / "src" / "lib" / "components" / "AudioUploader.svelte"
)


def component_source() -> str:
    assert COMPONENT.exists(), f"missing component source: {COMPONENT}"
    return COMPONENT.read_text(encoding="utf-8")


class TestSourceSelectorPresent:
    """The three recording-source options must stay in the UI."""

    def test_option_labels(self):
        src = component_source()
        for label in ("Микрофон", "Звук устройства", "Микрофон + устройство"):
            assert label in src, f"source option missing from UI: {label!r}"


class TestDeviceCapturePath:
    """Device audio must be captured via getDisplayMedia with audio enabled."""

    def test_get_display_media_audio(self):
        src = component_source()
        assert "getDisplayMedia" in src, "device-audio capture (getDisplayMedia) dropped"
        assert "audio: true" in src, "getDisplayMedia must request an audio track"

    def test_no_audio_track_is_handled(self):
        src = component_source()
        assert "No audio track captured" in src, (
            "sharing a surface without audio must fail with an explicit message, "
            "not a silent empty recording"
        )

    def test_video_tracks_stopped(self):
        src = component_source()
        assert "getVideoTracks().forEach" in src, (
            "getDisplayMedia video tracks must be stopped after extracting audio"
        )


class TestMixedMode:
    """Mic + device must mix into a single recorded stream."""

    def test_mixing_via_audio_context(self):
        src = component_source()
        assert "createMediaStreamDestination" in src, (
            "mixed mode must merge tracks via AudioContext into one stream"
        )
        assert "audio/webm;codecs=opus" in src, (
            "recorder must pin the opus mime type with a fallback"
        )


class TestCleanup:
    """Stopping the recording must release every captured track and context."""

    def test_onstop_releases_sources(self):
        src = component_source()
        assert "getTracks().forEach" in src
        assert "audioCtx?.close" in src, "AudioContext must be closed on stop"
