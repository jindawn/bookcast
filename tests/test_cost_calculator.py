import pytest
from pathlib import Path
from unittest.mock import patch
import json

from bookcast.cost import calculate_cost_summary
from bookcast.models import Attempt
from bookcast.generation import ProviderUsage
from bookcast.provider_config import ProvidersConfig, ModelPricing, PricingTier

def test_cost_calculation():
    config = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['mock-tts'],
        'providers': [
            {'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'deepseek-flash'},
            {'name': 'mock-tts', 'kind': 'tts', 'type': 'mock', 'model': 'gemini'}
        ],
        'pricing': {
            'deepseek-flash': {
                'off_peak': {'cached_input_per_million': 0.5, 'uncached_input_per_million': 1.0, 'output_per_million': 2.0},
                'peak': {'cached_input_per_million': 1.0, 'uncached_input_per_million': 2.0, 'output_per_million': 2.0}
            }
        }
    })
    
    class MockUsage:
        def __init__(self, c, i, o, r=0):
            self.cache_hit_tokens = c
            self.input_tokens = i
            self.output_tokens = o
            self.reasoning_tokens = r
            
    calls = [
        Attempt(id="1", task="book_synthesis", kind="llm", status="completed", provider="deepseek", model="deepseek-flash", prompt_version="1", input_hash="1",
                timestamp="2023-09-01T16:30:00Z", provider_reported_usage=ProviderUsage(cache_hit_tokens=500000, input_tokens=1500000, output_tokens=2000000, reasoning_tokens=500000)),
        Attempt(id="2", task="tts_segment:001", kind="tts", status="completed", provider="mock-tts", model="gemini-3.8-flash-tts", prompt_version="1", input_hash="1",
                timestamp="2023-09-01T16:30:00Z", provider_reported_usage=None, artifacts={"audio/segments/001-1.json": "hash"})
    ]
    
    summary = calculate_cost_summary(calls, config)
    
    # 500k cached = 0.25 (off peak)
    # 1M uncached = 1.0 (off peak)
    # 2M output = 4.0 (off peak)
    # Total = 5.25
    assert summary['llm']['providers']['deepseek::deepseek-flash']['cost']['amount'] == 5.25
    assert summary['llm']['providers']['deepseek::deepseek-flash']['usage']['reasoning_tokens'] == 500000
    assert summary['tts']['providers']['mock-tts::gemini-3.8-flash-tts']['usage_available'] is False
    assert summary['total']['status'] == 'partial'
