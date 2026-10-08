"""Чанкование длинного аудио: env-порог и нарезка oversized-сегментов диаризации.

Бэкенд ASR (LiteLLM → triton-openai-adapter) отвергает файлы >25 MiB —
это ~819 c WAV 16kHz-mono. Порог чанкования (600 c по умолчанию) должен
держать одиночный запрос ниже лимита, а монолог длиннее чанка — резаться
на подсегменты и склеиваться финальным мерджером обратно.
"""

from unittest.mock import MagicMock, patch

import pytest

from gigaam_transcriber.data_models import SpeakerSegment, TranscriptionSegment
from gigaam_transcriber.transcriber import (
    CHUNK_DURATION_SEC,
    CHUNK_THRESHOLD_SEC,
    GigaAMTranscriber,
    _env_float,
)

BYTES_PER_SECOND = 16000 * 2  # WAV 16kHz mono PCM16
BACKEND_FILE_LIMIT = 25 * 1024 * 1024


@pytest.fixture
def chunking_transcriber():
    transcriber = GigaAMTranscriber(api_key="test-key", hf_token="hf-token")

    provider = MagicMock()
    provider.transcribe.return_value = "кусок"
    provider.transcribe_segments.side_effect = (
        lambda audio_path, segments, language=None: [
            TranscriptionSegment(
                text=f"текст{i}",
                start=seg["start"],
                end=seg["end"],
                speaker=seg.get("speaker"),
            )
            for i, seg in enumerate(segments)
        ]
    )

    processor = MagicMock()
    processor.is_supported_file.return_value = True
    processor.is_video_file.return_value = False
    processor.get_media_info.return_value = {"sample_rate": 16000, "channels": 1}
    transcriber._audio_processor = processor

    with patch(
        "gigaam_transcriber.transcriber.get_asr_provider",
        return_value=provider,
    ):
        yield transcriber, provider


class TestChunkSizing:
    def test_defaults_keep_single_request_under_backend_limit(self):
        assert CHUNK_THRESHOLD_SEC == 600.0
        assert CHUNK_DURATION_SEC == 300.0
        assert CHUNK_THRESHOLD_SEC * BYTES_PER_SECOND < BACKEND_FILE_LIMIT
        assert CHUNK_DURATION_SEC * BYTES_PER_SECOND < BACKEND_FILE_LIMIT

    def test_env_float_parsing(self, monkeypatch):
        monkeypatch.setenv("X_SEC", "42.5")
        assert _env_float("X_SEC", 1.0) == 42.5

        monkeypatch.setenv("X_SEC", "not-a-number")
        assert _env_float("X_SEC", 7.0) == 7.0

        monkeypatch.setenv("X_SEC", "-5")
        assert _env_float("X_SEC", 7.0, minimum=1.0) == 1.0

        monkeypatch.delenv("X_SEC", raising=False)
        assert _env_float("X_SEC", 9.0) == 9.0


class TestWholeFileChunking:
    def test_audio_above_threshold_is_chunked(self, chunking_transcriber, temp_dir):
        transcriber, provider = chunking_transcriber
        transcriber._audio_processor.get_duration.return_value = 900.0

        chunks = [(temp_dir / f"c{i}.wav", i * 300.0, (i + 1) * 300.0) for i in range(3)]
        for path, _s, _e in chunks:
            path.write_bytes(b"chunk")
        transcriber._audio_processor.split_audio.return_value = chunks

        audio_file = temp_dir / "long.wav"
        audio_file.write_bytes(b"audio")
        result = transcriber.transcribe(audio_file, diarization="none", denoise="none")

        assert provider.transcribe.call_count == 3
        requested_paths = [call.args[0] for call in provider.transcribe.call_args_list]
        assert requested_paths == [str(path) for path, _s, _e in chunks]
        assert result.text == "кусок кусок кусок"

    def test_audio_below_threshold_stays_single_request(self, chunking_transcriber, temp_dir):
        transcriber, provider = chunking_transcriber
        transcriber._audio_processor.get_duration.return_value = 500.0

        audio_file = temp_dir / "medium.wav"
        audio_file.write_bytes(b"audio")
        transcriber.transcribe(audio_file, diarization="none", denoise="none")

        provider.transcribe.assert_called_once()
        transcriber._audio_processor.split_audio.assert_not_called()


class TestDiarizationSegmentSplit:
    def test_oversized_monologue_is_split_and_merged_back(self, chunking_transcriber, temp_dir):
        transcriber, provider = chunking_transcriber
        transcriber._audio_processor.get_duration.return_value = 750.0

        transcriber._diarization_manager = MagicMock()
        transcriber._diarization_manager.diarize.return_value = [
            SpeakerSegment(start=0.0, end=700.0, speaker="Спикер_0"),
            SpeakerSegment(start=700.0, end=750.0, speaker="Спикер_1"),
        ]

        audio_file = temp_dir / "meeting.wav"
        audio_file.write_bytes(b"audio")
        result = transcriber.transcribe(audio_file, diarization="pyannote", denoise="none")

        sent = provider.transcribe_segments.call_args.args[1]
        assert len(sent) > 2
        for seg in sent:
            assert seg["end"] - seg["start"] <= transcriber.chunk_duration + 1e-6

        sent_speaker0 = [s for s in sent if s["speaker"] == "Спикер_0"]
        for prev, nxt in zip(sent_speaker0, sent_speaker0[1:]):
            assert nxt["start"] == pytest.approx(prev["end"])
        assert sent_speaker0[0]["start"] == pytest.approx(0.0)
        assert sent_speaker0[-1]["end"] == pytest.approx(700.0)

        merged = [s for s in result.segments if s.speaker == "Спикер_0"]
        assert len(merged) == 1
        assert merged[0].start == pytest.approx(0.0)
        assert merged[0].end == pytest.approx(700.0)
        assert all(f"текст{i}" in merged[0].text for i in range(len(sent_speaker0)))

    def test_short_segments_are_not_split(self, chunking_transcriber, temp_dir):
        transcriber, provider = chunking_transcriber
        transcriber._audio_processor.get_duration.return_value = 60.0

        transcriber._diarization_manager = MagicMock()
        transcriber._diarization_manager.diarize.return_value = [
            SpeakerSegment(start=0.0, end=30.0, speaker="Спикер_0"),
            SpeakerSegment(start=30.5, end=60.0, speaker="Спикер_1"),
        ]

        audio_file = temp_dir / "talk.wav"
        audio_file.write_bytes(b"audio")
        transcriber.transcribe(audio_file, diarization="pyannote", denoise="none")

        sent = provider.transcribe_segments.call_args.args[1]
        assert [(s["start"], s["end"], s["speaker"]) for s in sent] == [
            (0.0, 30.0, "Спикер_0"),
            (30.5, 60.0, "Спикер_1"),
        ]
