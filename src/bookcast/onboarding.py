"""Safe first-run profiles and provider summaries shared by CLI and Web."""

import json
import os
from pathlib import Path

from .errors import BookCastError
from .provider_config import ProvidersConfig


PROFILES = {
    "demo": "完全离线的 Mock 流程；音频是测试音调，不是真人语音。",
    "deepseek-kokoro": "DeepSeek 云端 LLM + Kokoro 本地中文人声；可能产生 API 费用。",
    "deepseek-gemini": "DeepSeek 云端 LLM + Gemini 云端双人语音；两次发送文本，需显式同意。",
    "deepseek-qwen": "DeepSeek 云端 LLM + 实验性 Qwen 本地语音；需较多本机资源。",
}


def profile_text(profile: str, destination: Path, model_dir: Path | None = None) -> str:
    """Produce validated TOML with env *names* only; never copy credential values."""
    if profile not in PROFILES:
        raise BookCastError(f"未知配置方案：{profile}。运行 bookcast setup 查看可选方案。")
    lines = ['schema_version = 1']
    real = profile != "demo"
    lines += ['llm_priority = ["deepseek"]' if real else 'llm_priority = ["mock"]']
    voice = {"demo": "mock-tts", "deepseek-kokoro": "kokoro", "deepseek-gemini": "gemini",
             "deepseek-qwen": "qwen"}[profile]
    lines += [f'tts_priority = ["{voice}"]', '', '[[providers]]']
    if real:
        lines += ['name = "deepseek"', 'kind = "llm"', 'type = "openai-compatible"',
                  'model = "deepseek-flash"', 'base_url = "https://api.deepseek.com"',
                  'api_key_env = "DEEPSEEK_API_KEY"', 'timeout_seconds = 120',
                  'reasoning_policy = "bookcast-v1"', '[providers.generation]', 'max_tokens = 16384']
    else:
        lines += ['name = "mock"', 'kind = "llm"', 'type = "mock"', 'model = "mock-llm-v1"']
    lines += ['', '[[providers]]', f'name = "{voice}"', 'kind = "tts"']
    if profile == "demo":
        lines += ['type = "mock"', 'model = "mock-tones-v1"']
    elif profile == "deepseek-gemini":
        lines += ['type = "gemini-tts"', 'model = "gemini-3.8-flash-tts"',
                  'api_key_env = "GEMINI_API_KEY"', 'timeout_seconds = 120',
                  '[providers.cloud_tts]', 'send_text_to_cloud = true', 'data_tier = "unknown"',
                  'host_voice = "Kore"', 'guest_voice = "Puck"']
    else:
        if model_dir is None:
            raise BookCastError("本地语音方案需要模型目录。")
        relative = os.path.relpath(model_dir.expanduser().resolve(), destination.expanduser().resolve().parent)
        lines += ['type = "kokoro-local"' if voice == 'kokoro' else 'type = "qwen-local"',
                  'model = "kokoro-multi-lang-v1_0"' if voice == 'kokoro'
                  else 'model = "Qwen3-TTS-12Hz-1.7B-CustomVoice"', '[providers.local_tts]',
                  f'model_dir = {json.dumps(relative, ensure_ascii=False)}']
        if voice == 'qwen':
            lines += ['experimental = true', 'host_voice = "Vivian"', 'guest_voice = "Uncle_Fu"']
    lines.append('
[pricing.deepseek-flash.off_peak]
cached_input_per_million = 0.5
uncached_input_per_million = 1.0
output_per_million = 2.0

[pricing.deepseek-flash.peak]
cached_input_per_million = 1.0
uncached_input_per_million = 2.0
output_per_million = 2.0
')
    result = '\n'.join(lines) + '\n'
    import tomllib
    ProvidersConfig.model_validate(tomllib.loads(result))
    return result


def provider_summary(settings, reports: list[dict]) -> dict:
    """Only public status and env-variable *names* leave the process."""
    by_name = {report['provider']: report for report in reports}
    specs = {spec.name: spec for spec in settings.providers}
    selected = {}
    steps = []
    for kind, priority in (("llm", settings.llm_priority), ("tts", settings.tts_priority)):
        name = next((candidate for candidate in priority
                     if by_name.get(candidate, {}).get('availability') == 'available'), priority[0])
        spec = specs[name]
        report = by_name.get(name, {})
        cloud = spec.type in {'openai-compatible', 'gemini-tts'}
        mode = '测试' if spec.type == 'mock' else '云端' if cloud else '本地'
        available = report.get('availability') == 'available'
        selected[kind] = {'name': name, 'model': spec.model, 'mode': mode,
                          'experimental': spec.type == 'qwen-local', 'available': available,
                          'real': spec.type != 'mock'}
        if spec.api_key_env and not os.environ.get(spec.api_key_env):
            steps.append(f'在启动 BookCast 的终端设置 {spec.api_key_env} 环境变量；不要写入配置或网页。')
        elif spec.api_key_env and not available:
            steps.append(f'检查 {name} 的凭证、服务状态与额度；可在终端运行 bookcast doctor --human。')
        if spec.type == 'kokoro-local' and not available:
            steps.append('安装语音依赖和 Kokoro 模型：uv sync --extra tts；bookcast setup --profile deepseek-kokoro --install-model。')
        if spec.type == 'qwen-local' and not available:
            steps.append('实验性 Qwen 需要独立安装依赖、官方模型和可用 MPS；参阅 docs/TTS.md。')
        if spec.type == 'gemini-tts' and not available and os.environ.get(spec.api_key_env or ''):
            steps.append('检查 Gemini Developer API 凭证、访问权限和额度；该方案会向云端发送脚本文本。')
    return {'selected': selected, 'real_voice': selected['tts']['real'],
            'ready': all(value['available'] for value in selected.values()), 'missing_steps': steps,
            'privacy': '云端 Provider 会向第三方发送文本；Gemini TTS 仅在显式选择云端方案后启用。'}
