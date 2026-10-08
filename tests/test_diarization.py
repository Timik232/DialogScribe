#!/usr/bin/env python3
"""Environment-dependent diarization model smoke test."""

import sys

import pytest

pytestmark = [
    pytest.mark.requires_gpu,
    pytest.mark.requires_hf_token,
    pytest.mark.requires_model,
]


def test_pipeline_loading():
    """Load the configured pipeline only in the explicit model-test environment."""
    from gigaam_transcriber.diarization import DiarizationManager

    manager = DiarizationManager()
    assert manager.pipeline is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
