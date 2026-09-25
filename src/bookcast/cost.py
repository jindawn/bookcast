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
        # Peak: 工作日 09:00-12:00, 14:00-18:00
        # Off-peak: otherwise
        is_weekday = dt_bj.weekday() < 5 # 0-4 are Mon-Fri
        if is_weekday:
            if (9 <= dt_bj.hour < 12) or (14 <= dt_bj.hour < 18):
                return False
        return True
    except Exception:
        return False

def calculate_cost_summary(calls: list, config: ProvidersConfig, root_dir: Path | None = None, current_settings: dict | None = None) -> dict:
    summary = {
        "currency": "CNY",
        "llm": {
            "current_provider": None,
            "providers": defaultdict(lambda: {
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
            })
        },
        "tts": {
            "current_provider": None,
            "providers": defaultdict(lambda: {
                "model": None,
                "request_count": 0,
                "retry_count": 0,
                "audio_duration_seconds": 0.0,
                "usage_available": False,
                "cost": {
                    "amount": 0.0,
                    "status": "unavailable"
                }
            })
        },
        "total": {
            "known_amount": 0.0,
            "status": "partial"
        },
        "diagnostics": []
    }
    
    # 0. Set current providers
    if current_settings:
        conf = current_settings.get('config', {}) if isinstance(current_settings, dict) else {}
        llm_sel = current_settings.get('llm_selection', 'auto')
        tts_sel = current_settings.get('tts_selection', 'auto')
        
        if llm_sel == 'auto':
            summary['llm']['current_provider'] = conf.get('llm_priority', config.llm_priority)[0] if conf.get('llm_priority', config.llm_priority) else None
        else:
            summary['llm']['current_provider'] = llm_sel
            
        if tts_sel == 'auto':
            summary['tts']['current_provider'] = conf.get('tts_priority', config.tts_priority)[0] if conf.get('tts_priority', config.tts_priority) else None
        else:
            summary['tts']['current_provider'] = tts_sel
    else:
        summary['llm']['current_provider'] = config.llm_priority[0] if config.llm_priority else None
        summary['tts']['current_provider'] = config.tts_priority[0] if config.tts_priority else None

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
            
        prov = call.provider
        p_dict = summary['llm']['providers'][prov]
        p_dict['model'] = call.model
        p_dict['requests'] += 1
        p_dict['by_stage'][stage]['requests'] += 1
        
        usage = call.provider_reported_usage
        if usage:
            cached = getattr(usage, 'cache_hit_tokens', 0) or 0
            total_input = getattr(usage, 'input_tokens', 0) or 0
            uncached = max(0, total_input - cached)
            output = getattr(usage, 'output_tokens', 0) or 0
            reasoning = getattr(usage, 'reasoning_tokens', 0) or 0
            
            p_dict['usage']['cached_input_tokens'] += cached
            p_dict['usage']['uncached_input_tokens'] += uncached
            p_dict['usage']['output_tokens'] += output
            p_dict['usage']['reasoning_tokens'] += reasoning
            
            p_dict['by_stage'][stage]['usage']['cached_input_tokens'] += cached
            p_dict['by_stage'][stage]['usage']['uncached_input_tokens'] += uncached
            p_dict['by_stage'][stage]['usage']['output_tokens'] += output
            p_dict['by_stage'][stage]['usage']['reasoning_tokens'] += reasoning
            
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
                p_dict['cost']['status'] = 'unavailable'
                p_dict['by_stage'][stage]['cost']['status'] = 'unavailable'
            else:
                if cost_status == 'estimated':
                    p_dict['cost']['status'] = 'estimated'
                    p_dict['by_stage'][stage]['cost']['status'] = 'estimated'
                p_dict['cost']['amount'] += amount
                p_dict['by_stage'][stage]['cost']['amount'] += amount

    # 2. Process TTS calls
    import json
    tts_audio = set()
    for call in calls:
        if call.kind != 'tts' or call.status not in {'completed', 'failed_retryable', 'failed_permanent'}:
            continue
            
        prov = call.provider
        p_dict = summary['tts']['providers'][prov]
        p_dict['model'] = call.model
        p_dict['request_count'] += 1
        
        if call.status != 'completed':
            p_dict['retry_count'] += 1
            
        usage = call.provider_reported_usage
        if usage and (getattr(usage, 'input_tokens', None) is not None or getattr(usage, 'output_tokens', None) is not None):
            p_dict['usage_available'] = True
            
        if call.status == 'completed' and call.artifacts and root_dir:
            for artifact in call.artifacts:
                if artifact.endswith('.json') and artifact not in tts_audio:
                    tts_audio.add(artifact)
                    try:
                        with open(root_dir / artifact) as af:
                            data = json.load(af)
                            p_dict['audio_duration_seconds'] += data.get('duration_seconds', 0.0)
                    except Exception:
                        pass
                        
    # Ensure current providers are in the dict even if no calls yet
    cp_llm = summary['llm']['current_provider']
    if cp_llm and cp_llm not in summary['llm']['providers']:
        summary['llm']['providers'][cp_llm] = summary['llm']['providers'][cp_llm] # init default
        
    cp_tts = summary['tts']['current_provider']
    if cp_tts and cp_tts not in summary['tts']['providers']:
        summary['tts']['providers'][cp_tts] = summary['tts']['providers'][cp_tts] # init default

    # 3. Calculate Totals
    total_amount = 0.0
    total_status = 'actual'
    
    # We sum all LLM costs (both current and historical)
    for p_dict in summary['llm']['providers'].values():
        if p_dict['cost']['status'] == 'unavailable':
            total_status = 'unavailable'
        else:
            total_amount += p_dict['cost']['amount']
            if p_dict['cost']['status'] == 'estimated':
                total_status = 'estimated'
            p_dict['cost']['amount'] = round(p_dict['cost']['amount'], 4)
            
    for p_dict in summary['tts']['providers'].values():
        if p_dict['cost']['status'] == 'unavailable':
            total_status = 'partial'
        
    summary['total']['known_amount'] = round(total_amount, 4)
    summary['total']['status'] = total_status

    # Convert default dicts
    summary['llm']['providers'] = dict(summary['llm']['providers'])
    for p_dict in summary['llm']['providers'].values():
        p_dict['by_stage'] = {k: dict(v) for k, v in p_dict['by_stage'].items()}
        for k in p_dict['by_stage']:
            p_dict['by_stage'][k]['cost']['amount'] = round(p_dict['by_stage'][k]['cost']['amount'], 4)
            
    summary['tts']['providers'] = dict(summary['tts']['providers'])

    # 4. Diagnostics
    for stage, retries in stage_retries.items():
        if retries > 3 and retries / max(1, stage_requests[stage]) > 0.2:
            summary['diagnostics'].append(f"成本异常提示：本次 {stage} 阶段重试 {retries} 次，可能增加额外费用。")

    return summary

