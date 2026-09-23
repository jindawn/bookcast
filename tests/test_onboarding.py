"""First-run config, privacy and compatible doctor/Web contracts."""

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.provider_config import load_config
from bookcast.web_api import create_app


def test_setup_demo_is_exclusive_and_creates_runnable_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    listing = runner.invoke(app, ['setup'])
    assert listing.exit_code == 0 and '测试音调' in listing.output
    result = runner.invoke(app, ['setup', '--profile', 'demo'])
    assert result.exit_code == 0, result.output
    assert load_config().llm_priority == ['mock']
    original = (tmp_path / 'bookcast.toml').read_bytes()
    assert runner.invoke(app, ['setup', '--profile', 'demo']).exit_code == 1
    assert (tmp_path / 'bookcast.toml').read_bytes() == original
    doctor = runner.invoke(app, ['doctor'])
    assert doctor.exit_code == 0 and json.loads(doctor.stdout)['onboarding']['real_voice'] is False
    human = runner.invoke(app, ['doctor', '--human'])
    assert human.exit_code == 0 and '测试音调' in human.stdout and '✓' in human.stdout


def test_real_profiles_require_explicit_cloud_and_experimental_consent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'DO-NOT-PRINT-KEY')
    runner = CliRunner()
    for name, flag in [('deepseek-gemini', '--allow-cloud-tts'),
                       ('deepseek-qwen', '--allow-experimental')]:
        path = tmp_path / f'{name}.toml'
        command = ['setup', '--profile', name, '--config-output', str(path)]
        assert runner.invoke(app, command).exit_code == 1
        assert not path.exists()
        if name.endswith('qwen'):
            command += ['--model-dir', str(tmp_path / 'qwen model')]
        result = runner.invoke(app, [*command, flag])
        assert result.exit_code == 0, result.output
        settings = load_config(path)
        assert settings.llm_priority == ['deepseek']
        assert 'DO-NOT-PRINT-KEY' not in path.read_text() + result.output
    gemini = load_config(tmp_path / 'deepseek-gemini.toml').providers[-1]
    assert gemini.cloud_tts.send_text_to_cloud
    assert load_config(tmp_path / 'deepseek-qwen.toml').providers[-1].local_tts.experimental


def test_kokoro_profile_and_web_report_without_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    path = tmp_path / 'bookcast.toml'
    result = CliRunner().invoke(app, ['setup', '--profile', 'deepseek-kokoro'])
    assert result.exit_code == 0, result.output
    settings = load_config(path)
    assert settings.providers[-1].local_tts.model_dir == str(tmp_path / 'data/models/kokoro-multi-lang-v1_0')
    doctor = CliRunner().invoke(app, ['doctor'])
    data = json.loads(doctor.stdout)
    assert doctor.exit_code == 1
    assert data['onboarding']['selected']['llm']['mode'] == '云端'
    assert data['onboarding']['selected']['tts']['real'] is True
    assert any('DEEPSEEK_API_KEY' in action for action in data['onboarding']['missing_steps'])
    monkeypatch.setenv('UNRELATED_SECRET', 'WEB-SECRET-NOT-RETURNED')
    with TestClient(create_app(tmp_path / 'web', path), base_url='http://127.0.0.1') as client:
        response = client.get('/api/providers')
    assert response.status_code == 200
    assert 'WEB-SECRET-NOT-RETURNED' not in response.text
    assert response.json()['onboarding']['selected']['tts']['name'] == 'kokoro'
