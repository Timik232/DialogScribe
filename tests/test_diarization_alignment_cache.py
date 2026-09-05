"""
Offline-тесты выравнивания меток и кэша моделей диаризации (Task 16).

Модели не загружаются: torchaudio подменяется фейком в sys.modules,
фабрики speechbrain/pyannote патчатся на уровне классов.
"""

import contextlib
import sys
import threading
import time
import unittest
from unittest import mock

import torch

from gigaam_transcriber import diarization as dz
from gigaam_transcriber.diarization import (
    SPEECHBRAIN_EMBEDDING_REVISION,
    SPEAKER_UNKNOWN,
    DiarizationManager,
    HybridDiarization,
    _model_cache,
    release_diarization_models,
)

SAMPLE_RATE = 16000


class FakeEmbeddingModel:
    """Модель-заглушка: эмбеддинг выводится из среднего значения сэмплов."""

    def __init__(self):
        self.call_shapes = []
        self.lock_probe = None

    def encode_batch(self, segment):
        self.call_shapes.append(tuple(segment.shape))
        if self.lock_probe is not None:
            self.lock_probe.append(self.lock_probe_lock().locked())
        mean = float(segment.double().mean().item())
        speaker_b_weight = mean - 1.0
        return torch.tensor([[1.0 - speaker_b_weight, speaker_b_weight]])


def make_hybrid_audio(speaker_a_regions, speaker_b_regions, total_seconds=8.0):
    """Волна: регионы спикера A = 1.0, спикера B = 2.0, тишина = 0."""
    waveform = torch.zeros(1, int(SAMPLE_RATE * total_seconds))
    for start, end in speaker_a_regions:
        waveform[0, int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)] = 1.0
    for start, end in speaker_b_regions:
        waveform[0, int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)] = 2.0
    return waveform


def fake_torchaudio(waveform, sample_rate=SAMPLE_RATE):
    module = mock.MagicMock(name="torchaudio")
    module.load.return_value = (waveform, sample_rate)
    return module


@contextlib.contextmanager
def patched_torchaudio(waveform):
    """Подмена torchaudio одним ключом sys.modules.

    mock.patch.dict(sys.modules, ...) на выходе удаляет модули,
    импортированные ВНУТРИ блока (numpy/sklearn) — это ломает
    повторный импорт C-расширений в других тестах.
    """
    saved = sys.modules.get("torchaudio")
    sys.modules["torchaudio"] = fake_torchaudio(waveform)
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("torchaudio", None)
        else:
            sys.modules["torchaudio"] = saved


class AlignmentTestCase(unittest.TestCase):
    """CQ-H4: метки кластеров применяются только к сегментам с эмбеддингами."""

    def setUp(self):
        release_diarization_models()

    def _run_diarize(self, speech_segments, waveform, num_speakers=2):
        hybrid = HybridDiarization(device="cpu")
        fake_model = FakeEmbeddingModel()
        _model_cache.get_or_load(hybrid._cache_key(), lambda: fake_model)
        with patched_torchaudio(waveform):
            result = hybrid.diarize("fake.wav", speech_segments, num_speakers=num_speakers)
        return result, fake_model

    def test_interleaved_short_segments_keep_order_and_labels(self):
        speech_segments = [
            (0.0, 2.0),
            (2.0, 2.3),
            (2.5, 4.5),
            (4.6, 4.8),
            (5.0, 7.0),
        ]
        waveform = make_hybrid_audio(
            speaker_a_regions=[(0.0, 2.0), (5.0, 7.0)],
            speaker_b_regions=[(2.5, 4.5)],
        )

        result, fake_model = self._run_diarize(speech_segments, waveform)

        self.assertEqual(len(result), 5)
        self.assertEqual(
            [(seg.start, seg.end) for seg in result], speech_segments
        )
        # эмбеддинги построены только для длинных сегментов 0, 2, 4
        self.assertEqual(len(fake_model.call_shapes), 3)

        speaker_0 = result[0].speaker
        speaker_2 = result[2].speaker
        self.assertNotEqual(speaker_0, speaker_2)
        self.assertEqual(result[4].speaker, speaker_0)

        # короткий сегмент 1: зазор до сегмента 0 = 0, до сегмента 2 = 0.2
        self.assertEqual(result[1].speaker, speaker_0)
        # короткий сегмент 3: зазор до сегмента 2 = 0.1, до сегмента 4 = 0.2
        self.assertEqual(result[3].speaker, speaker_2)

    def test_old_zip_bug_would_shift_labels(self):
        """Регрессия: zip против неотфильтрованного списка ронял сегменты."""
        speech_segments = [
            (0.0, 2.0),
            (2.0, 2.3),
            (2.5, 4.5),
            (4.6, 4.8),
            (5.0, 7.0),
        ]
        waveform = make_hybrid_audio(
            speaker_a_regions=[(0.0, 2.0), (5.0, 7.0)],
            speaker_b_regions=[(2.5, 4.5)],
        )

        result, _ = self._run_diarize(speech_segments, waveform)

        self.assertEqual(len(result), 5)
        for seg in result:
            self.assertTrue(seg.speaker)
            self.assertNotEqual(seg.speaker, SPEAKER_UNKNOWN)

    def test_all_short_segments_fallback_single_speaker(self):
        speech_segments = [(0.0, 0.2), (0.3, 0.45)]
        waveform = make_hybrid_audio([], [])

        result, fake_model = self._run_diarize(speech_segments, waveform)

        self.assertEqual(len(result), 2)
        self.assertEqual(fake_model.call_shapes, [])
        self.assertTrue(all(seg.speaker == "Спикер №1" for seg in result))
        self.assertEqual([(s.start, s.end) for s in result], speech_segments)

    def test_single_long_segment_fallback(self):
        result, fake_model = self._run_diarize(
            [(0.0, 2.0)], make_hybrid_audio([(0.0, 2.0)], [])
        )

        self.assertEqual(len(result), 1)
        # эмбеддинг построен, но для кластеризации записей недостаточно
        self.assertEqual(len(fake_model.call_shapes), 1)
        self.assertEqual(result[0].speaker, "Спикер №1")

    def test_nearest_labeled_speaker_empty_returns_unknown(self):
        self.assertEqual(
            HybridDiarization._nearest_labeled_speaker(
                1.0, 2.0, {}, [(1.0, 2.0)]
            ),
            SPEAKER_UNKNOWN,
        )

    def test_nearest_labeled_speaker_tie_prefers_lower_index(self):
        labeled = {5: "Спикер №2", 1: "Спикер №1"}
        # двоично точные границы: оба зазора равны 0.5
        segments = [
            (0.0, 0.4),
            (0.5, 1.0),
            (1.1, 1.2),
            (1.3, 1.4),
            (2.1, 2.2),
            (2.5, 3.0),
        ]
        self.assertEqual(
            HybridDiarization._nearest_labeled_speaker(1.5, 2.0, labeled, segments),
            "Спикер №1",
        )

    def test_nearest_labeled_speaker_overlap_beats_gap(self):
        labeled = {0: "Спикер №1", 2: "Спикер №2"}
        segments = [(0.0, 2.0), (1.5, 1.6), (2.1, 3.0)]
        self.assertEqual(
            HybridDiarization._nearest_labeled_speaker(1.5, 1.6, labeled, segments),
            "Спикер №1",
        )


class ModelCacheTestCase(unittest.TestCase):
    """CQ-M6: процесс-широкий кэш моделей и сериализация инференса."""

    def setUp(self):
        release_diarization_models()

    def tearDown(self):
        release_diarization_models()

    def _patch_hybrid_factory(self, calls, delay=0.05):
        def factory():
            calls.append(threading.current_thread().name)
            time.sleep(delay)
            return FakeEmbeddingModel()

        patcher = mock.patch.object(
            HybridDiarization, "_load_embedding_model", side_effect=factory
        )
        return patcher

    def test_concurrent_instances_load_once_per_key(self):
        calls = []
        barrier = threading.Barrier(8)
        results = {}
        results_lock = threading.Lock()

        def worker(name):
            hybrid = HybridDiarization(device="cpu")
            barrier.wait()
            model = hybrid._get_embedding_model()
            with results_lock:
                results[name] = model

        with self._patch_hybrid_factory(calls):
            threads = [
                threading.Thread(target=worker, args=(f"t{i}",), name=f"t{i}")
                for i in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 8)
        self.assertEqual(len({id(model) for model in results.values()}), 1)
        self.assertEqual(len(_model_cache), 1)
        self.assertIn(
            ("speechbrain", SPEECHBRAIN_EMBEDDING_REVISION, "cpu", "float32"),
            _model_cache.keys(),
        )

    def test_distinct_keys_load_separately(self):
        calls = []
        with self._patch_hybrid_factory(calls):
            HybridDiarization(device="cpu")._get_embedding_model()
            HybridDiarization(device="cuda")._get_embedding_model()
            HybridDiarization(device="cpu", dtype="float16")._get_embedding_model()

        self.assertEqual(len(calls), 3)
        self.assertEqual(len(_model_cache), 3)
        self.assertEqual(
            sorted(key[2] for key in _model_cache.keys()),
            ["cpu", "cpu", "cuda"],
        )

    def test_pyannote_pipeline_cached_across_managers(self):
        calls = []

        class FakePipeline:
            def __call__(self, path, **kwargs):
                return object()

        def factory():
            calls.append(threading.current_thread().name)
            return FakePipeline()

        with mock.patch.object(DiarizationManager, "_load_pipeline", side_effect=factory):
            first = DiarizationManager(hf_token="token", device="cpu")
            second = DiarizationManager(hf_token="token", device="cpu")
            self.assertIs(first.pipeline, second.pipeline)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(_model_cache), 1)
        self.assertIn(
            ("pyannote", dz.PYANNOTE_PIPELINE_REVISION, "cpu", "float32"),
            _model_cache.keys(),
        )

    def test_pyannote_cold_cache_loads_before_inference_lock(self):
        calls = []

        class FakeAnnotation:
            def itertracks(self, yield_label=False):
                yield type("Turn", (), {"start": 0.0, "end": 1.0})(), None, "SPEAKER_0"

        class FakePipeline:
            def __call__(self, path, **kwargs):
                return FakeAnnotation()

        def factory():
            calls.append(True)
            return FakePipeline()

        manager = DiarizationManager(hf_token="token", device="cpu")
        with mock.patch.object(
            DiarizationManager, "_load_pipeline", side_effect=factory
        ):
            first = manager.diarize("fake.wav")
            second = manager.diarize("fake.wav")

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [(segment.start, segment.end) for segment in first], [(0.0, 1.0)]
        )
        self.assertEqual(
            [(segment.start, segment.end) for segment in second], [(0.0, 1.0)]
        )

    def test_hybrid_inference_lock_covers_encode_call(self):
        hybrid = HybridDiarization(device="cpu")
        fake_model = FakeEmbeddingModel()

        with mock.patch.object(
            HybridDiarization,
            "_load_embedding_model",
            side_effect=lambda: fake_model,
        ):
            hybrid._get_embedding_model()

        probe_lock = _model_cache.inference_lock(hybrid._cache_key())
        observed = []
        fake_model.lock_probe = observed
        fake_model.lock_probe_lock = lambda: probe_lock

        waveform = make_hybrid_audio([(0.0, 2.0)], [(2.5, 4.5)])
        with patched_torchaudio(waveform):
            hybrid.diarize(
                "fake.wav",
                [(0.0, 2.0), (2.5, 4.5)],
                num_speakers=2,
            )

        self.assertEqual(observed, [True, True])
        self.assertFalse(probe_lock.locked())

    def test_pyannote_inference_lock_covers_pipeline_call(self):
        observed = []
        lock_holder = {}

        class ProbePipeline:
            def __call__(self, path, **kwargs):
                observed.append(lock_holder["lock"].locked())
                return object()

        manager = DiarizationManager(hf_token="token", device="cpu")
        with mock.patch.object(
            DiarizationManager, "_load_pipeline", side_effect=lambda: ProbePipeline()
        ):
            manager.pipeline
            lock_holder["lock"] = _model_cache.inference_lock(manager._cache_key())
            result = manager.diarize("fake.wav")

        self.assertEqual(observed, [True])
        self.assertEqual(result, [])
        self.assertFalse(lock_holder["lock"].locked())

    def test_auto_device_resolves_to_cpu_when_cuda_unavailable(self):
        with mock.patch("torch.cuda.is_available", return_value=False):
            hybrid = HybridDiarization(device="auto")
            manager = DiarizationManager(hf_token="token", device="auto")

        self.assertEqual(hybrid.device, "cpu")
        self.assertEqual(manager.device, "cpu")

        calls = []
        with self._patch_hybrid_factory(calls):
            hybrid._get_embedding_model()

        self.assertEqual(len(calls), 1)
        self.assertIn(
            ("speechbrain", SPEECHBRAIN_EMBEDDING_REVISION, "cpu", "float32"),
            _model_cache.keys(),
        )

        with mock.patch("torch.cuda.is_available", return_value=True):
            resolved_key = hybrid._cache_key()
        # устройство вычисляется один раз при создании инстанса
        self.assertEqual(resolved_key[2], "cpu")

    def test_release_is_idempotent_and_reloads_lazily(self):
        calls = []
        with self._patch_hybrid_factory(calls):
            HybridDiarization(device="cpu")._get_embedding_model()

        first_release = release_diarization_models()
        second_release = release_diarization_models()

        self.assertEqual(len(first_release), 1)
        self.assertEqual(second_release, [])
        self.assertEqual(len(_model_cache), 0)

        with self._patch_hybrid_factory(calls):
            HybridDiarization(device="cpu")._get_embedding_model()

        self.assertEqual(len(calls), 2)

    def test_transcriber_cleanup_releases_model_cache(self):
        from gigaam_transcriber.transcriber import GigaAMTranscriber

        transcriber = GigaAMTranscriber(api_key="test-key")
        transcriber._diarization_manager = DiarizationManager(
            hf_token="token", device="cpu"
        )
        calls = []
        with self._patch_hybrid_factory(calls):
            HybridDiarization(device="cpu")._get_embedding_model()
        self.assertEqual(len(_model_cache), 1)

        transcriber.cleanup()
        self.assertEqual(len(_model_cache), 0)
        # повторный вызов идемпотентен
        transcriber.cleanup()


if __name__ == "__main__":
    unittest.main()
