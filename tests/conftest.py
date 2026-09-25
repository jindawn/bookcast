"""Require every test module to declare its execution/cost tier."""

from __future__ import annotations

import pytest
import socket
from types import SimpleNamespace
from pathlib import Path
import os


# Keep each module in one tier. Provider contract, filesystem, and CLI compositions
# are integration tests even when an individual assertion is small.
UNIT_MODULES = {
    "test_source_http", "test_validate_project", "test_qwen_spike",
}
INTEGRATION_MODULES = {
    "test_content", "test_export", "test_gemini_tts", "test_gemini_interactions", "test_generation",
    "test_job_cli", "test_job_recovery", "test_ocr", "test_onboarding", "test_phase1",
    "test_providers", "test_qwen_tts", "test_skill", "test_sources", "test_tts",
    "test_tts_ab", "test_tts_voice_test", "test_tts_final", "test_tts_integration", "test_gemini_retry", "test_cost_calculator", "test_cost_regression", "test_cost_isolation", "test_cost_integration", "test_gemini_safety_accounting", "test_web",
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


@pytest.fixture(autouse=True)
def forbid_offline_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately if an offline test reaches a real socket."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("large_model"):
        return

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline test attempted real network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    guard_dir = str(Path(__file__).parent / "offline_guard")
    monkeypatch.setenv("BOOKCAST_TEST_OFFLINE", "1")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (guard_dir, os.environ.get("PYTHONPATH")))))


@pytest.fixture(autouse=True)
def fake_gemini_time(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline tests keep the production interval but advance virtual time."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("large_model"):
        return
    from bookcast.adapters import gemini

    current = [1000.0]

    def clock() -> float:
        return current[0]

    def sleeper(seconds: float) -> None:
        current[0] += seconds

    monkeypatch.setattr(gemini, "time", SimpleNamespace(time=clock, sleep=sleeper))
    monkeypatch.setattr(gemini.GeminiTTSProvider, "_last_request_time", 0.0)


@pytest.fixture(autouse=True)
def isolate_default_cli_config(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI tests that exercise the implicit Mock default ignore a user's TOML."""
    if request.node.name in {
        "test_cli_modes_invalid_config_and_acquire_bridge",
        "test_cli_generate_and_status",
        "test_cli_local_file_generate_and_resume",
    }:
        monkeypatch.chdir(request.getfixturevalue("tmp_path"))
