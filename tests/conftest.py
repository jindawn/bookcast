"""Require every test module to declare its execution/cost tier."""

from __future__ import annotations

import pytest


# Keep each module in one tier. Provider contract, filesystem, and CLI compositions
# are integration tests even when an individual assertion is small.
UNIT_MODULES = {
    "test_source_http", "test_validate_project", "test_qwen_spike",
}
INTEGRATION_MODULES = {
    "test_content", "test_export", "test_gemini_tts", "test_generation",
    "test_job_cli", "test_job_recovery", "test_ocr", "test_onboarding", "test_phase1",
    "test_providers", "test_qwen_tts", "test_skill", "test_sources", "test_tts",
    "test_tts_ab", "test_web",
}
LIVE_MODULES = {
    "test_live_deepseek", "test_live_gemini", "test_live_kokoro", "test_live_qwen",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag tests centrally so adding a module cannot silently join default CI."""
    unknown: list[str] = []
    for item in items:
        module = item.path.stem
        if module in LIVE_MODULES:
            item.add_marker(pytest.mark.live)
        elif module == "test_release_large_book":
            item.add_marker(pytest.mark.large_model)
        elif module in UNIT_MODULES:
            item.add_marker(pytest.mark.unit)
        elif module in INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)
        else:
            unknown.append(module)
    if unknown:
        raise pytest.UsageError(
            "Tests must be assigned to a cost tier in tests/conftest.py: "
            + ", ".join(sorted(set(unknown)))
        )
