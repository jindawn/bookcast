import pytest
from pathlib import Path
from bookcast.cost import calculate_cost_summary
from bookcast.models import Attempt
from bookcast.provider_config import ProvidersConfig
from bookcast.generation import ProviderUsage

def test_provider_grouping_and_attribution():
    config = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['deepseek'], 'tts_priority': ['gemini'],
        'providers': [
            {'name': 'deepseek', 'kind': 'llm', 'type': 'mock', 'model': 'deepseek-flash'},
            {'name': 'gemini', 'kind': 'tts', 'type': 'mock', 'model': 'gemini'},
            {'name': 'kokoro', 'kind': 'tts', 'type': 'mock', 'model': 'kokoro'}
        ]
    })
    
    calls = [
        # historical kokoro calls
        Attempt(id="1", task="tts_segment:001", kind="tts", status="completed", provider="kokoro", model="kokoro-v1", prompt_version="1", input_hash="1",
                timestamp="2023-09-01T16:30:00Z", provider_reported_usage=None),
        Attempt(id="2", task="tts_segment:002", kind="tts", status="completed", provider="kokoro", model="kokoro-v1", prompt_version="1", input_hash="1",
                timestamp="2023-09-01T16:30:00Z", provider_reported_usage=None),
    ]
    
    summary = calculate_cost_summary(calls, config, None)
    
    assert 'kokoro::kokoro-v1' in summary['tts']['providers']
    assert summary['tts']['providers']['kokoro::kokoro-v1']['request_count'] == 2
    
    assert 'gemini::gemini' in summary['tts']['providers']
    assert summary['tts']['providers']['gemini::gemini']['request_count'] == 0
    assert summary['tts']['current_provider'] == 'gemini'
    
    assert summary['llm']['current_provider'] == 'deepseek'

def test_deepseek_prices_and_reasoning_tokens():
    config = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['deepseek'], 'tts_priority': ['gemini'],
        'providers': [
            {'name': 'deepseek', 'kind': 'llm', 'type': 'mock', 'model': 'deepseek-flash'},
            {'name': 'gemini', 'kind': 'tts', 'type': 'mock', 'model': 'gemini'}
        ],
        'pricing': {
            'deepseek-flash': {
                'off_peak': {'cached_input_per_million': 0.02, 'uncached_input_per_million': 1.0, 'output_per_million': 4.0},
                'peak': {'cached_input_per_million': 0.04, 'uncached_input_per_million': 2.0, 'output_per_million': 8.0}
            }
        }
    })
    
    # 1 million each. 1M cached, 1M uncached, 1M reasoning, 2M output (reasoning is included in output by provider)
    # Off-peak pricing (00:00-08:00 Beijing time) -> 16:00-00:00 UTC -> 16:30 Z is off-peak
    calls_off_peak = [
        Attempt(id="3", task="book_synthesis", kind="llm", status="completed", provider="deepseek", model="deepseek-flash", prompt_version="1", input_hash="1",
                timestamp="2023-09-01T16:30:00Z", provider_reported_usage=ProviderUsage(cache_hit_tokens=1000000, input_tokens=2000000, output_tokens=2000000, reasoning_tokens=1000000)),
    ]
    summary_off = calculate_cost_summary(calls_off_peak, config)
    # 1M cached * 0.02 = 0.02
    # 1M uncached * 1.0 = 1.0
    # 2M output * 4.0 = 8.0
    # Total = 9.02
    assert summary_off['llm']['providers']['deepseek::deepseek-flash']['cost']['amount'] == 9.02
    assert summary_off['llm']['providers']['deepseek::deepseek-flash']['usage']['reasoning_tokens'] == 1000000
    
    # Peak pricing: Monday 14:30 Beijing time -> 06:30 Z (2026-09-28 is a Monday)
    calls_peak = [
        Attempt(id="4", task="book_synthesis", kind="llm", status="completed", provider="deepseek", model="deepseek-flash", prompt_version="1", input_hash="1",
                timestamp="2026-09-28T06:30:00Z", provider_reported_usage=ProviderUsage(cache_hit_tokens=1000000, input_tokens=2000000, output_tokens=2000000, reasoning_tokens=1000000)),
    ]
    summary_peak = calculate_cost_summary(calls_peak, config)
    # 1M cached * 0.04 = 0.04
    # 1M uncached * 2.0 = 2.0
    # 2M output * 8.0 = 16.0
    # Total = 18.04
    assert summary_peak['llm']['providers']['deepseek::deepseek-flash']['cost']['amount'] == 18.04
