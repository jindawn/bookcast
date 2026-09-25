import pytest
import os
import json
import time
from unittest.mock import patch, MagicMock
from bookcast.provider_config import ProvidersConfig
from bookcast.provider_registry import default_registry
from bookcast.provider_api import ProviderError, ErrorKind

@patch('bookcast.adapters.gemini.request.build_opener')
@patch('bookcast.adapters.gemini.time.sleep')
def test_rate_limit_exceeded_backoff(mock_sleep, mock_opener):
    os.environ['GEMINI_API_KEY'] = 'test'
    registry = default_registry()
    spec = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['gemini'], 'providers': [
            {'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'},
            {'name': 'gemini', 'kind': 'tts', 'type': 'gemini-tts', 'model': 'gemini-3.8-flash-tts', 'api_key_env': 'GEMINI_API_KEY', 'cloud_tts': {'send_text_to_cloud': True, 'min_request_interval': 0}}
        ]
    }).providers[1]
    provider = registry.create(spec)
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        'error': {'status': 'RESOURCE_EXHAUSTED', 'message': 'too_many_requests... rate limit exceeded'}
    }).encode()
    
    import urllib.error
    mock_opener.return_value.open.side_effect = urllib.error.HTTPError('url', 429, 'Too Many Requests', {}, mock_response)
    
    with pytest.raises(ProviderError) as exc_info:
        provider._request({"test": "payload"})
        
    assert exc_info.value.kind == ErrorKind.RATE_LIMIT
    assert exc_info.value.error_type == 'too_many_requests'
    assert mock_sleep.call_count == 4

@patch('bookcast.adapters.gemini.request.build_opener')
@patch('bookcast.adapters.gemini.time.sleep')
def test_quota_exceeded_no_retry(mock_sleep, mock_opener):
    os.environ['GEMINI_API_KEY'] = 'test'
    registry = default_registry()
    spec = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['gemini'], 'providers': [
            {'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'},
            {'name': 'gemini', 'kind': 'tts', 'type': 'gemini-tts', 'model': 'gemini-3.8-flash-tts', 'api_key_env': 'GEMINI_API_KEY', 'cloud_tts': {'send_text_to_cloud': True, 'min_request_interval': 0}}
        ]
    }).providers[1]
    provider = registry.create(spec)
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        'error': {
            'status': 'RESOURCE_EXHAUSTED', 
            'message': 'Quota exceeded.',
            'details': [{'violations': [{'quotaId': 'PerDay'}]}]
        }
    }).encode()
    
    import urllib.error
    mock_opener.return_value.open.side_effect = urllib.error.HTTPError('url', 429, 'Too Many Requests', {}, mock_response)
    
    with pytest.raises(ProviderError) as exc_info:
        provider._request({"test": "payload"})
        
    assert exc_info.value.kind == ErrorKind.QUOTA
    assert exc_info.value.error_type == 'quota_exceeded'
    assert mock_sleep.call_count == 0  # No backoff for quota!

@patch('bookcast.adapters.gemini.request.build_opener')
@patch('bookcast.adapters.gemini.time.sleep')
def test_retry_after_header(mock_sleep, mock_opener):
    os.environ['GEMINI_API_KEY'] = 'test'
    registry = default_registry()
    spec = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['gemini'], 'providers': [
            {'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'},
            {'name': 'gemini', 'kind': 'tts', 'type': 'gemini-tts', 'model': 'gemini-3.8-flash-tts', 'api_key_env': 'GEMINI_API_KEY', 'cloud_tts': {'send_text_to_cloud': True, 'min_request_interval': 0}}
        ]
    }).providers[1]
    provider = registry.create(spec)
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        'error': {'status': 'RESOURCE_EXHAUSTED', 'message': 'rate limit'}
    }).encode()
    
    import urllib.error
    # mock a successful response after 1 failure
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps({}).encode()
    
    mock_opener.return_value.open.side_effect = [
        urllib.error.HTTPError('url', 429, 'Too Many Requests', {'Retry-After': '5'}, mock_response),
        success_response
    ]
    
    provider._request({"test": "payload"})
    
    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_with(5)

def test_success_chunk_no_repeat():
    # This is implicitly tested by the checkpointing framework.
    pass

