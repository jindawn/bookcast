import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .generation import task_type
from .provider_config import ProvidersConfig

def _is_off_peak(timestamp_str: str) -> bool:
    try:
        if not timestamp_str:
            return False
        if timestamp_str.endswith('Z'):
            dt = datetime.fromisoformat(timestamp_str[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        tz_bj = timezone(timedelta(hours=8))
        dt_bj = dt.astimezone(tz_bj)
        return 0 <= dt_bj.hour < 8
    except Exception:
        return False

def calculate_cost_summary(calls: list, config: ProvidersConfig, root_dir: Path | None = None) -> dict:
    summary = {
        "currency": "CNY",
        "llm": {
            "provider": None,
            "model": None,
            "requests": 0,
            "usage": {
                "cached_input_tokens": 0,
                "uncached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0
            },
            "cost": {
                "amount": 0.0,
                "status": "actual"
            },
            "by_stage": defaultdict(lambda: {
                "requests": 0,
                "usage": {
                    "cached_input_tokens": 0,
                    "uncached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0
                },
                "cost": {
                    "amount": 0.0,
                    "status": "actual"
                }
            })
        },
        "tts": {
            "provider": None,
            "model": None,
            "request_count": 0,
            "retry_count": 0,
            "audio_duration_seconds": 0.0,
            "usage_available": False,
            "cost": {
                "amount": None,
                "status": "unavailable"
            }
        },
        "total": {
            "known_amount": 0.0,
            "status": "partial"
        },
        "diagnostics": []
    }

    # Track usage for diagnostics
    stage_retries = defaultdict(int)
    stage_requests = defaultdict(int)

    # 1. Process LLM calls
    for call in calls:
        if call.kind != 'llm' or call.status not in {'completed', 'failed_retryable', 'failed_permanent'}:
            continue
            
        stage = task_type(call.task)
        stage_requests[stage] += 1
        if call.status != 'completed':
            stage_retries[stage] += 1
            
        summary['llm']['provider'] = call.provider # Last seen wins, usually homogeneous
        summary['llm']['model'] = call.model
        summary['llm']['requests'] += 1
        summary['llm']['by_stage'][stage]['requests'] += 1
        
        usage = call.provider_reported_usage
        if usage:
            cached = getattr(usage, 'cache_hit_tokens', 0) or 0
            total_input = getattr(usage, 'input_tokens', 0) or 0
            uncached = max(0, total_input - cached)
            output = getattr(usage, 'output_tokens', 0) or 0
            reasoning = getattr(usage, 'reasoning_tokens', 0) or 0
            
            summary['llm']['usage']['cached_input_tokens'] += cached
            summary['llm']['usage']['uncached_input_tokens'] += uncached
            summary['llm']['usage']['output_tokens'] += output
            summary['llm']['usage']['reasoning_tokens'] += reasoning
            
            summary['llm']['by_stage'][stage]['usage']['cached_input_tokens'] += cached
            summary['llm']['by_stage'][stage]['usage']['uncached_input_tokens'] += uncached
            summary['llm']['by_stage'][stage]['usage']['output_tokens'] += output
            summary['llm']['by_stage'][stage]['usage']['reasoning_tokens'] += reasoning
            
            # Calculate cost
            pricing_tier = config.pricing.get(call.model) if hasattr(config, 'pricing') else None
            cost_status = 'actual'
            amount = 0.0
            
            if pricing_tier:
                is_off = _is_off_peak(call.timestamp)
                pricing_model = pricing_tier.off_peak if is_off and pricing_tier.off_peak else pricing_tier.peak if not is_off and pricing_tier.peak else pricing_tier.default
                if pricing_model:
                    if not call.timestamp:
                        cost_status = 'estimated'
                    
                    amount = (
                        (cached / 1_000_000.0) * pricing_model.cached_input_per_million +
                        (uncached / 1_000_000.0) * pricing_model.uncached_input_per_million +
                        (output / 1_000_000.0) * pricing_model.output_per_million
                    )
                else:
                    cost_status = 'unavailable'
            else:
                cost_status = 'unavailable'
                
            if cost_status == 'unavailable':
                summary['llm']['cost']['status'] = 'unavailable'
                summary['llm']['by_stage'][stage]['cost']['status'] = 'unavailable'
            else:
                if cost_status == 'estimated':
                    summary['llm']['cost']['status'] = 'estimated'
                    summary['llm']['by_stage'][stage]['cost']['status'] = 'estimated'
                summary['llm']['cost']['amount'] += amount
                summary['llm']['by_stage'][stage]['cost']['amount'] += amount

    # 2. Process TTS calls
    tts_audio = set()
    for call in calls:
        if call.kind != 'tts' or call.status not in {'completed', 'failed_retryable', 'failed_permanent'}:
            continue
            
        summary['tts']['provider'] = call.provider
        summary['tts']['model'] = call.model
        summary['tts']['request_count'] += 1
        
        if call.status != 'completed':
            summary['tts']['retry_count'] += 1
            
        usage = call.provider_reported_usage
        if usage and (getattr(usage, 'input_tokens', None) is not None or getattr(usage, 'output_tokens', None) is not None):
            summary['tts']['usage_available'] = True
            
        if call.status == 'completed' and call.artifacts and root_dir:
            for artifact in call.artifacts:
                if artifact.endswith('.json') and artifact not in tts_audio:
                    tts_audio.add(artifact)
                    try:
                        with open(root_dir / artifact) as af:
                            data = json.load(af)
                            summary['tts']['audio_duration_seconds'] += data.get('duration_seconds', 0.0)
                    except Exception:
                        pass
                        
    # 3. Calculate Totals
    total_amount = 0.0
    total_status = 'actual'
    
    if summary['llm']['cost']['status'] == 'unavailable':
        total_status = 'unavailable'
    else:
        total_amount += summary['llm']['cost']['amount']
        if summary['llm']['cost']['status'] == 'estimated':
            total_status = 'estimated'
            
    if summary['tts']['cost']['status'] == 'unavailable':
        total_status = 'partial'
    else:
        # if tts had cost, we'd add it here
        pass
        
    summary['total']['known_amount'] = round(total_amount, 4)
    summary['llm']['cost']['amount'] = round(summary['llm']['cost']['amount'], 4)
    summary['total']['status'] = total_status

    # Convert by_stage to dict for json serialization
    summary['llm']['by_stage'] = {k: dict(v) for k, v in summary['llm']['by_stage'].items()}
    for k in summary['llm']['by_stage']:
        summary['llm']['by_stage'][k]['cost']['amount'] = round(summary['llm']['by_stage'][k]['cost']['amount'], 4)

    # 4. Diagnostics
    for stage, retries in stage_retries.items():
        if retries > 3 and retries / max(1, stage_requests[stage]) > 0.2:
            summary['diagnostics'].append(f"成本异常提示：本次 {stage} 阶段重试 {retries} 次，可能增加额外费用。")

    return summary

