"""
Тесты для модуля transcriber (основной класс). 

Тестирует GigaAMTranscriber, использующий Mistral Voxtral API.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from gigaam_transcriber import (
    GigaAMTranscriber,
    create_transcriber,
    TranscriptionResult,
    TranscriptionSegment,
    UnsupportedFormatError,
    ASRError,
)


class TestGigaAMTranscriberInit:
    """Тесты инициализации GigaAMTranscriber."""

    def test_default_init(self):
        """Тест инициализации с параметрами по умолчанию."""
        transcriber = GigaAMTranscriber()

        assert transcriber.asr_model == "voxtral-mini-latest"
        assert transcriber.asr_url == "https://api.mistral.ai"

    def test_custom_asr_model(self):
        """Тест с кастомной моделью ASR."""
        transcriber = GigaAMTranscriber(asr_model="voxtral-large-latest")

        assert transcriber.asr_model == "voxtral-large-latest"

    def test_custom_asr_url(self):
        """Тест с кастомным URL ASR."""
        transcriber = GigaAMTranscriber(asr_url="https://custom-asr.example.com")

        assert transcriber.asr_url == "https://custom-asr.example.com"

    def test_api_key_from_env(self):
        """Тест получения MISTRAL_API_KEY из переменной окружения."""
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key-123"}):
            transcriber = GigaAMTranscriber()

            assert transcriber.api_key == "test-key-123"

    def test_api_key_explicit(self):
        """Тест с явным указанием API ключа."""
        transcriber = GigaAMTranscriber(api_key="explicit-key")

        assert transcriber.api_key == "explicit-key"

    def test_api_key_priority(self):
        """Тест приоритета явного ключа над env."""
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "env-key"}):
            transcriber = GigaAMTranscriber(api_key="explicit-key")

            assert transcriber.api_key == "explicit-key"

    def test_hf_token_from_env(self):
        """Тест получения HF_TOKEN из переменной окружения."""
        with patch.dict(os.environ, {"HF_TOKEN": "test_token"}):
            transcriber = GigaAMTranscriber()

            assert transcriber.hf_token == "test_token"

    def test_hf_token_explicit(self):
        """Тест с явным указанием токена."""
        transcriber = GigaAMTranscriber(hf_token="explicit_token")

        assert transcriber.hf_token == "explicit_token"

    def test_cache_dir_default(self):
        """Тест директории кэша по умолчанию."""
        transcriber = GigaAMTranscriber()

        assert transcriber.cache_dir.exists()
        assert "gigaam_transcriber" in str(transcriber.cache_dir)

    def test_cache_dir_custom(self, temp_dir):
        """Тест с кастомной директорией кэша."""
        cache_dir = temp_dir / "custom_cache"
        transcriber = GigaAMTranscriber(cache_dir=cache_dir)

        assert transcriber.cache_dir == cache_dir
        assert cache_dir.exists()


class TestGigaAMTranscriberContextManager:
    """Тесты контекстного менеджера."""

    def test_context_manager_enter(self):
        """Тест входа в контекст."""
        with GigaAMTranscriber() as transcriber:
            assert isinstance(transcriber, GigaAMTranscriber)

    def test_context_manager_cleanup(self):
        """Тест очистки при выходе из контекста."""
        transcriber = GigaAMTranscriber()
        transcriber.cleanup()

    def test_context_manager_exit_closes_client(self):
        """Тест что __exit__ вызывает cleanup."""
        transcriber = GigaAMTranscriber()
        transcriber.__exit__(None, None, None)


class TestGigaAMTranscriberLazyLoading:
    """Тесты ленивой загрузки."""

    def test_asr_client_lazy(self):
        """Тест что ASR провайдер создаётся через фабрику, без персистентного клиента."""
        transcriber = GigaAMTranscriber(api_key="test")

        assert not hasattr(transcriber, "_asr_client")
        assert transcriber._min_request_interval == 1.0

        transcriber.cleanup()

    def test_audio_processor_lazy(self):
        """Тест ленивой загрузки аудио процессора."""
        transcriber = GigaAMTranscriber()

        assert transcriber._audio_processor is None

        with patch("gigaam_transcriber.transcriber.AudioProcessor") as MockAP:
            mock_ap = MagicMock()
            MockAP.return_value = mock_ap

            processor = transcriber.audio_processor

            assert processor is mock_ap
            assert transcriber._audio_processor is mock_ap


class TestGigaAMTranscriberGetModelInfo:
    """Тесты метода get_model_info."""

    def test_get_model_info(self):
        """Тест получения информации о конфигурации ASR."""
        transcriber = GigaAMTranscriber(api_key="test-key")
        info = transcriber.get_model_info()

        assert "model_name" in info
        assert "asr_url" in info
        assert "provider" in info
        assert info["model_name"] == "voxtral-mini-latest"
        assert info["asr_url"] == "https://api.mistral.ai"
        assert info["provider"] == "per-request (via factory)"
        assert info["api_key_set"] is True


class TestGigaAMTranscriberValidation:
    """Тесты валидации входных данных."""

    def test_validate_unsupported_format(self, temp_dir):
        """Тест с неподдерживаемым форматом."""
        transcriber = GigaAMTranscriber()

        bad_file = temp_dir / "test.xyz"
        bad_file.write_text("test")

        with patch("gigaam_transcriber.transcriber.AudioProcessor") as MockAP:
            mock_ap = MagicMock()
            mock_ap.is_supported_file.return_value = False
            MockAP.return_value = mock_ap

            with pytest.raises(UnsupportedFormatError):
                transcriber._validate_input(bad_file)

    def test_validate_nonexistent_file(self):
        """Тест с несуществующим файлом."""
        transcriber = GigaAMTranscriber()

        with pytest.raises(FileNotFoundError):
            transcriber._validate_input(Path("/nonexistent/file.wav"))


class TestCreateTranscriberFunction:
    """Тесты для функции create_transcriber."""

    def test_create_default(self):
        """Тест создания с параметрами по умолчанию."""
        transcriber = create_transcriber()

        assert isinstance(transcriber, GigaAMTranscriber)
        assert transcriber.asr_model == "voxtral-mini-latest"

    def test_create_with_params(self):
        """Тест создания с параметрами."""
        transcriber = create_transcriber(
            api_key="test-key",
            asr_url="https://custom.example.com",
            asr_model="voxtral-large-latest",
            hf_token="test-hf",
        )

        assert transcriber.asr_model == "voxtral-large-latest"
        assert transcriber.asr_url == "https://custom.example.com"
        assert transcriber.api_key == "test-key"
        assert transcriber.hf_token == "test-hf"


class TestGigaAMTranscriberMocked:
    """Тесты с моками (без реальных запросов к Mistral API)."""

    @pytest.fixture
    def mock_transcriber(self):
        """Фикстура транскрибера с замоканным ASR провайдером."""
        transcriber = GigaAMTranscriber(api_key="test-key")

        mock_provider = MagicMock()
        mock_provider.transcribe.return_value = "Тестовая транскрипция"
        mock_provider.transcribe_segments.return_value = [
            TranscriptionSegment(
                text="Первый сегмент",
                start=0.0,
                end=5.0,
                speaker="Спикер_0",
            ),
            TranscriptionSegment(
                text="Второй сегмент",
                start=5.0,
                end=10.0,
                speaker="Спикер_1",
            ),
        ]

        transcriber._mock_provider = mock_provider

        mock_processor = MagicMock()
        mock_processor.is_audio_file.return_value = True
        mock_processor.is_video_file.return_value = False
        mock_processor.is_supported_file.return_value = True
        mock_processor.get_duration.return_value = 10.0
        mock_processor.get_media_info.return_value = {
            "duration": 10.0,
            "sample_rate": 16000,
            "channels": 1,
        }

        transcriber._audio_processor = mock_processor

        with patch(
            "gigaam_transcriber.transcriber.get_asr_provider",
            return_value=mock_provider,
        ):
            yield transcriber

    def test_transcribe_no_diarization(self, mock_transcriber, temp_dir):
        """Тест транскрипции без диаризации (весь файл одним запросом)."""
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")

        result = mock_transcriber.transcribe(audio_file, diarization="none")

        assert isinstance(result, TranscriptionResult)
        assert len(result.segments) == 1
        assert result.segments[0].text == "Тестовая транскрипция"
        assert result.model_name == "voxtral-mini-latest"

    def test_transcribe_returns_duration(self, mock_transcriber, temp_dir):
        """Тест что результат содержит длительность."""
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")

        result = mock_transcriber.transcribe(audio_file)

        assert result.duration == 10.0

    def test_transcribe_empty_text_raises(self, mock_transcriber, temp_dir):
        """Тест что пустой текст от ASR вызывает ошибку."""
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")

        mock_transcriber._mock_provider.transcribe.return_value = ""

        from gigaam_transcriber.exceptions import EmptyAudioError

        with pytest.raises(EmptyAudioError):
            mock_transcriber.transcribe(audio_file, diarization="none")

    def test_audio2text(self, mock_transcriber, temp_dir):
        """Тест метода audio2text."""
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio content")

        result = mock_transcriber.audio2text(audio_file)

        assert isinstance(result, TranscriptionResult)
        assert result.text == "Тестовая транскрипция"

    def test_video2text(self, mock_transcriber, temp_dir):
        """Тест метода video2text."""
        video_file = temp_dir / "test.mp4"
        video_file.write_bytes(b"fake video content")

        mock_transcriber._audio_processor.is_video_file.return_value = True
        mock_transcriber._audio_processor.is_audio_file.return_value = False
        mock_transcriber._audio_processor.extract_audio_from_video.return_value = (
            temp_dir / "extracted.wav"
        )
        # Создаём извлечённый аудио файл
        (temp_dir / "extracted.wav").write_bytes(b"fake extracted audio")

        # Для извлечённого аудио используем те же моки
        mock_transcriber._audio_processor.get_duration.return_value = 10.0
        mock_transcriber._audio_processor.get_media_info.return_value = {
            "sample_rate": 16000,
            "channels": 1,
        }

        result = mock_transcriber.video2text(video_file)

        assert isinstance(result, TranscriptionResult)

    def test_get_model_info_loaded(self, mock_transcriber):
        """Тест get_model_info с загруженным клиентом."""
        info = mock_transcriber.get_model_info()

        assert info["api_key_set"] is True
        assert info["model_name"] == "voxtral-mini-latest"

    def test_transcribe_batch(self, mock_transcriber, temp_dir):
        """Тест пакетной транскрипции."""
        files = []
        for i in range(3):
            f = temp_dir / f"test_{i}.wav"
            f.write_bytes(b"fake audio")
            files.append(f)

        results = mock_transcriber.transcribe_batch(files, diarization="none")

        assert len(results) == 3
        for result in results:
            assert isinstance(result, TranscriptionResult)

    def test_transcribe_batch_with_error(self, mock_transcriber, temp_dir):
        """Тест пакетной транскрипции с ошибкой в одном файле."""
        files = []
        for i in range(3):
            f = temp_dir / f"test_{i}.wav"
            f.write_bytes(b"fake audio")
            files.append(f)

        # Второй файл вызовет ошибку
        original_transcribe = mock_transcriber.transcribe
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise ASRError("test error")
            return original_transcribe(*args, **kwargs)

        mock_transcriber.transcribe = side_effect

        results = mock_transcriber.transcribe_batch(files, diarization="none")

        # Все файлы обработаны (с ошибкой или без)
        assert len(results) == 3


class TestASROptionForwarding:
    """CQ-H3: language/denoise доезжают до провайдера во всех путях транскрипции."""

    @pytest.fixture
    def forwarding_transcriber(self):
        transcriber = GigaAMTranscriber(api_key="test-key", hf_token="hf-token")

        mock_provider = MagicMock()
        mock_provider.transcribe.return_value = "Текст"
        mock_provider.transcribe_segments.return_value = [
            TranscriptionSegment(text="Сегмент", start=0.0, end=2.0, speaker="Спикер_0"),
        ]

        mock_processor = MagicMock()
        mock_processor.is_supported_file.return_value = True
        mock_processor.is_video_file.return_value = False
        mock_processor.get_duration.return_value = 10.0
        mock_processor.get_media_info.return_value = {"sample_rate": 16000, "channels": 1}

        transcriber._audio_processor = mock_processor
        transcriber._mock_provider = mock_provider

        with patch(
            "gigaam_transcriber.transcriber.get_asr_provider",
            return_value=mock_provider,
        ):
            yield transcriber, mock_provider

    def test_short_path_forwards_options(self, forwarding_transcriber, temp_dir):
        transcriber, provider = forwarding_transcriber
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio")

        result = transcriber.transcribe(
            audio_file, diarization="none", language="en", denoise="rnnoise",
        )

        assert result.language == "en"
        provider.transcribe.assert_called_once()
        kwargs = provider.transcribe.call_args.kwargs
        assert kwargs["language"] == "en"
        assert kwargs["diarization"] is False
        assert kwargs["denoise"] is True

    def test_chunked_path_forwards_options(self, forwarding_transcriber, temp_dir):
        transcriber, provider = forwarding_transcriber
        audio_file = temp_dir / "long.wav"
        audio_file.write_bytes(b"fake audio")
        transcriber.chunk_threshold = 5.0
        chunk1 = temp_dir / "chunk_0.wav"
        chunk2 = temp_dir / "chunk_1.wav"
        chunk1.write_bytes(b"c1")
        chunk2.write_bytes(b"c2")
        transcriber._audio_processor.split_audio.return_value = [
            (chunk1, 0.0, 5.0), (chunk2, 5.0, 10.0),
        ]

        transcriber.transcribe(
            audio_file, diarization="none", language="fr", denoise="none",
        )

        assert provider.transcribe.call_count == 2
        for call in provider.transcribe.call_args_list:
            assert call.kwargs["language"] == "fr"
            assert call.kwargs["diarization"] is False
            assert call.kwargs["denoise"] is False

    def test_video_path_forwards_options(self, forwarding_transcriber, temp_dir):
        transcriber, provider = forwarding_transcriber
        video_file = temp_dir / "test.mp4"
        video_file.write_bytes(b"fake video")
        extracted = temp_dir / "extracted.wav"
        extracted.write_bytes(b"fake extracted audio")
        transcriber._audio_processor.is_video_file.return_value = True
        transcriber._audio_processor.extract_audio_from_video.return_value = extracted

        transcriber.transcribe(
            video_file, diarization="none", language="de", denoise="none",
        )

        provider.transcribe.assert_called_once()
        kwargs = provider.transcribe.call_args.kwargs
        assert kwargs["language"] == "de"

    def test_diarized_path_forwards_language(self, forwarding_transcriber, temp_dir):
        from gigaam_transcriber.data_models import SpeakerSegment

        transcriber, provider = forwarding_transcriber
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio")
        transcriber._diarization_manager = MagicMock()
        transcriber._diarization_manager.diarize.return_value = [
            SpeakerSegment(start=0.0, end=2.0, speaker="Спикер_0"),
        ]

        result = transcriber.transcribe(
            audio_file, diarization="pyannote", language="es", denoise="none",
        )

        assert result.language == "es"
        provider.transcribe_segments.assert_called_once()
        assert provider.transcribe_segments.call_args.kwargs["language"] == "es"


class TestGigaAMTranscriberASRError:
    """Тесты обработки ошибок ASR."""

    def test_asr_provider_called_correctly(self, temp_dir):
        """Тест что ASR провайдер вызывается через фабрику."""
        audio_file = temp_dir / "test.wav"
        audio_file.write_bytes(b"fake audio")

        mock_provider = MagicMock()
        mock_provider.transcribe.return_value = "Текст"

        with patch(
            "gigaam_transcriber.transcriber.get_asr_provider",
            return_value=mock_provider,
        ) as mock_factory:
            transcriber = GigaAMTranscriber(
                api_key="my-key",
                asr_url="https://custom.api",
                asr_model="voxtral-large",
            )

            mock_processor = MagicMock()
            mock_processor.is_supported_file.return_value = True
            mock_processor.is_video_file.return_value = False
            mock_processor.get_duration.return_value = 5.0
            mock_processor.get_media_info.return_value = {
                "sample_rate": 16000,
                "channels": 1,
            }
            transcriber._audio_processor = mock_processor

            result = transcriber.transcribe(audio_file, diarization="none")

            mock_factory.assert_called_once()
            mock_provider.transcribe.assert_called_once()
            mock_provider.close.assert_called_once()
