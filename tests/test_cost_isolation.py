import pytest
from pathlib import Path
from bookcast.cost import calculate_cost_summary
from bookcast.models import Attempt
from bookcast.provider_config import ProvidersConfig
from bookcast.generation import ProviderUsage

def test_job_isolation_identity():
    config = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['deepseek'], 'tts_priority': ['gemini'],
        'providers': [
            {'name': 'deepseek', 'kind': 'llm', 'type': 'mock', 'model': 'deepseek-flash'},
            {'name': 'gemini', 'kind': 'tts', 'type': 'mock', 'model': 'gemini'}
        ],
        'pricing': {
            'deepseek-flash': {
                'peak': {'cached_input_per_million': 0.04, 'uncached_input_per_million': 2.0, 'output_per_million': 8.0}
            }
        }
    })
    
    calls_a = [
        Attempt(id="A1", task="book_synthesis", kind="llm", status="completed", provider="deepseek", model="deepseek-flash", prompt_version="1", input_hash="1",
                timestamp="2026-09-28T06:30:00Z", provider_reported_usage=ProviderUsage(cache_hit_tokens=100, input_tokens=100, output_tokens=200, reasoning_tokens=0)),
    ]
    manifest_info_a = {"job_id": "job_a", "output_id": "out_a"}
    
    calls_b = [
        Attempt(id="B1", task="book_synthesis", kind="llm", status="completed", provider="deepseek", model="deepseek-flash", prompt_version="1", input_hash="1",
                timestamp="2026-09-28T06:30:00Z", provider_reported_usage=ProviderUsage(cache_hit_tokens=999, input_tokens=999, output_tokens=888, reasoning_tokens=0)),
    ]
    manifest_info_b = {"job_id": "job_b", "output_id": "out_b"}
    
    summary_a = calculate_cost_summary(calls_a, config, None, None, manifest_info_a)
    summary_b = calculate_cost_summary(calls_b, config, None, None, manifest_info_b)
    
    assert summary_a['job_identity']['job_id'] == 'job_a'
    assert summary_a['llm']['providers']['deepseek::deepseek-flash']['usage']['cached_input_tokens'] == 100
    assert summary_a['llm']['providers']['deepseek::deepseek-flash']['usage']['output_tokens'] == 200
    
    assert summary_b['job_identity']['job_id'] == 'job_b'
    assert summary_b['llm']['providers']['deepseek::deepseek-flash']['usage']['cached_input_tokens'] == 999
    assert summary_b['llm']['providers']['deepseek::deepseek-flash']['usage']['output_tokens'] == 888
