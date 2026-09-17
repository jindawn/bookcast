"""Offline safety checks; no Qwen dependency, download, or synthesis in CI."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/spikes/qwen_native.py"


def test_spike_rejects_modified_weights(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("qwen_spike", SCRIPT)
    spike = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spike)
    clean = b"test fixture, not a real model"
    monkeypatch.setattr(spike, "WEIGHTS", {"model.safetensors": hashlib.sha256(clean).hexdigest()})
    path = tmp_path / "model.safetensors"
    path.write_bytes(clean)
    spike.verify_weights(tmp_path)
    path.write_bytes(clean + b"corruption")
    with pytest.raises(ValueError, match="hash mismatch"):
        spike.verify_weights(tmp_path)


def test_spike_refuses_online_environment_before_import_or_write(tmp_path):
    output = tmp_path / "output"
    env = {key: value for key, value in os.environ.items()
           if key not in {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}}
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "missing"), str(output)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "offline environment required" in result.stderr
    assert not output.exists()
