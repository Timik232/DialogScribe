#!/usr/bin/env python3
"""Run non-external tests while making known baseline failures explicit."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

KNOWN_FAILURES = {
    "tests/test_audio_processor.py::TestAudioProcessorDenoise::test_normalize_denoise_light_arnndn",
    "tests/test_user_system_e2e.py::TestUsageLimitsEnforcement::test_limit_not_exceeded_passes",
}


class FailureInventory:
    def __init__(self) -> None:
        self.failed: set[str] = set()
        self.collection_errors: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" and report.failed:
            self.failed.add(report.nodeid)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_errors.add(report.nodeid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, default=Path(".ci-artifacts/python-tests.xml"))
    args, pytest_args = parser.parse_known_args()
    args.junit.parent.mkdir(parents=True, exist_ok=True)

    inventory = FailureInventory()
    result = pytest.main(["-q", f"--junitxml={args.junit}", *pytest_args], plugins=[inventory])
    unexpected = inventory.failed - KNOWN_FAILURES
    if inventory.collection_errors:
        print("UNEXPECTED COLLECTION ERRORS:", *sorted(inventory.collection_errors), sep="\n  ")
        return 1
    if unexpected:
        print("UNEXPECTED TEST FAILURES:", *sorted(unexpected), sep="\n  ")
        return 1
    for nodeid in sorted(inventory.failed):
        print(f"KNOWN BASELINE FAILURE: {nodeid}")
    resolved = KNOWN_FAILURES - inventory.failed
    for nodeid in sorted(resolved):
        print(f"KNOWN BASELINE NOW PASSES: {nodeid}")
    if result not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        return int(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
