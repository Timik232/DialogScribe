import pytest

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment


def _make_result() -> TranscriptionResult:
    segments = [
        TranscriptionSegment(text="Hello", start=0.0, end=1.0, speaker="SPEAKER_00"),
        TranscriptionSegment(text="Hi there", start=1.5, end=2.5, speaker="SPEAKER_01"),
        TranscriptionSegment(text="How are you?", start=3.0, end=4.0, speaker="SPEAKER_00"),
    ]
    return TranscriptionResult(
        text="SPEAKER_00: Hello SPEAKER_01: Hi there SPEAKER_00: How are you?",
        segments=segments,
        duration=4.0,
        language="ru",
        model_name="test",
        processing_time=1.0,
    )


SPEAKER_NAMES = {"SPEAKER_00": "Анна", "SPEAKER_01": "Борис"}


class TestSpeakerNamingTxt:
    def test_names_appear_in_txt(self):
        result = _make_result()
        output = result.to_txt(speaker_names=SPEAKER_NAMES)
        assert "Анна" in output
        assert "Борис" in output
        assert "SPEAKER_00" not in output
        assert "SPEAKER_01" not in output

    def test_without_names_original(self):
        result = _make_result()
        output = result.to_txt()
        assert "SPEAKER_00" in output
        assert "SPEAKER_01" in output

    def test_no_timestamps_with_names(self):
        result = _make_result()
        output = result.to_txt(include_timestamps=False, speaker_names=SPEAKER_NAMES)
        assert "Анна: Hello" in output
        assert "Борис: Hi there" in output


class TestSpeakerNamingSrt:
    def test_names_appear_in_srt(self):
        result = _make_result()
        output = result.to_srt(speaker_names=SPEAKER_NAMES)
        assert "Анна" in output
        assert "Борис" in output
        assert "SPEAKER_00" not in output

    def test_without_names_original(self):
        result = _make_result()
        output = result.to_srt()
        assert "SPEAKER_00" in output


class TestSpeakerNamingVtt:
    def test_names_appear_in_vtt(self):
        result = _make_result()
        output = result.to_vtt(speaker_names=SPEAKER_NAMES)
        assert "Анна" in output
        assert "Борис" in output
        assert "SPEAKER_00" not in output

    def test_without_names_original(self):
        result = _make_result()
        output = result.to_vtt()
        assert "SPEAKER_00" in output


class TestSpeakerNamingExportPipeline:
    def test_result_from_dict_substitutes_names(self):
        from routers.exports import _result_from_dict

        data = {
            "text": "SPEAKER_00 says hello SPEAKER_01 says hi",
            "segments": [
                {"text": "hello", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                {"text": "hi", "start": 1.5, "end": 2.5, "speaker": "SPEAKER_01"},
            ],
            "duration": 2.5,
            "language": "ru",
        }
        result = _result_from_dict(data, speaker_names=SPEAKER_NAMES)
        assert result.segments[0].speaker == "Анна"
        assert result.segments[1].speaker == "Борис"
        assert "Анна" in result.text
        assert "Борис" in result.text
        assert "SPEAKER_00" not in result.text

    def test_result_from_dict_without_names(self):
        from routers.exports import _result_from_dict

        data = {
            "text": "SPEAKER_00: hello",
            "segments": [
                {"text": "hello", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
            ],
            "duration": 1.0,
            "language": "ru",
        }
        result = _result_from_dict(data)
        assert result.segments[0].speaker == "SPEAKER_00"
        assert "SPEAKER_00" in result.text


class TestBackwardCompatibility:
    def test_export_without_speaker_names(self):
        from routers.exports import _result_from_dict

        data = {
            "text": "hello world",
            "segments": [
                {"text": "hello", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                {"text": "world", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
            ],
            "duration": 2.0,
            "language": "ru",
        }
        result = _result_from_dict(data, speaker_names=None)
        assert result.segments[0].speaker == "SPEAKER_00"
        assert result.segments[1].speaker == "SPEAKER_01"

    def test_to_txt_without_speaker_names_param(self):
        result = _make_result()
        output = result.to_txt()
        assert "SPEAKER_00" in output
        assert "SPEAKER_01" in output

    def test_to_srt_without_speaker_names_param(self):
        result = _make_result()
        output = result.to_srt()
        assert "SPEAKER_00" in output

    def test_to_vtt_without_speaker_names_param(self):
        result = _make_result()
        output = result.to_vtt()
        assert "SPEAKER_00" in output


class TestAnalysisPropagation:
    def test_speaker_names_replaced_in_text(self):
        text = "SPEAKER_00 says hello SPEAKER_01 says hi SPEAKER_00 says bye"
        speaker_names = {"SPEAKER_00": "Анна", "SPEAKER_01": "Борис"}
        for original, name in speaker_names.items():
            text = text.replace(original, name)
        assert "Анна says hello" in text
        assert "Борис says hi" in text
        assert "Анна says bye" in text
        assert "SPEAKER_00" not in text
        assert "SPEAKER_01" not in text
