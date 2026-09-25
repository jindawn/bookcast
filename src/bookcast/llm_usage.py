"""Privacy-safe LLM usage totals derived from the durable attempt journal."""
from collections import defaultdict

from .generation import task_type


def usage_snapshot(calls, reuse_counts=None, prices=None):
    """Count terminal requests once, including billed failures and repairs.

    ``prices`` optionally maps (provider, model) to per-million-token input,
    cached-input and output prices. No price means cost is unknown, not zero.
    """
    reuse_counts, prices = reuse_counts or {}, prices or {}
    rows = defaultdict(lambda: {'request_count': 0, 'input_tokens': 0,
                                'cached_input_tokens': 0, 'output_tokens': 0,
                                'reasoning_tokens': 0, 'unknown_usage_count': 0})
    for call in calls:
        if call.kind != 'llm' or call.status not in {'completed', 'failed_retryable', 'failed_permanent'}:
            continue
        row = rows[(call.provider, call.model, task_type(call.task))]
        row['request_count'] += 1
        usage = call.provider_reported_usage
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            row['unknown_usage_count'] += 1
        if usage is not None:
            for target, source in (('input_tokens', 'input_tokens'), ('cached_input_tokens', 'cache_hit_tokens'),
                                   ('output_tokens', 'output_tokens'), ('reasoning_tokens', 'reasoning_tokens')):
                row[target] += getattr(usage, source) or 0
    fields = ('request_count', 'input_tokens', 'cached_input_tokens', 'output_tokens',
              'reasoning_tokens', 'unknown_usage_count')
    by_stage = defaultdict(lambda: {key: 0 for key in fields})
    entries = []
    for (provider, model, stage), row in sorted(rows.items()):
        for key in fields:
            by_stage[stage][key] += row[key]
        price = prices.get((provider, model))
        cost = None
        if price is not None and row['unknown_usage_count'] == 0:
            uncached = max(0, row['input_tokens'] - row['cached_input_tokens'])
            cost = round((uncached * price['input'] + row['cached_input_tokens'] * price['cached_input']
                          + row['output_tokens'] * price['output']) / 1_000_000, 6)
        entries.append({'provider': provider, 'model': model, 'stage': stage,
                        **row, 'estimated_cost': cost})
    totals = {key: sum(row[key] for row in rows.values()) for key in fields}
    totals['cache_reuse_count'] = sum(reuse_counts.values())
    totals['estimated_cost'] = (round(sum(entry['estimated_cost'] for entry in entries), 6)
                                if entries and all(entry['estimated_cost'] is not None for entry in entries) else None)
    for stage, count in reuse_counts.items():
        by_stage[stage]['cache_reuse_count'] = count
    return {'schema_version': 1, 'total': totals,
            'by_stage': {stage: {**row, 'cache_reuse_count': reuse_counts.get(stage, 0)}
                         for stage, row in sorted(by_stage.items())},
            'by_provider_model_stage': entries}


def tts_usage_snapshot(calls, root_dir=None):
    from collections import defaultdict
    import json
    rows = defaultdict(lambda: {
        'request_count': 0, 'input_tokens': 0, 'output_tokens': 0,
        'retry_count': 0, 'duration_seconds': 0.0
    })
    
    for call in calls:
        if call.kind != 'tts' or call.status not in {'completed', 'failed_retryable', 'failed_permanent'}:
            continue
            
        key = (call.provider, call.model)
        row = rows[key]
        row['request_count'] += 1
        
        if call.status == 'failed_retryable':
            row['retry_count'] += 1
            
        usage = call.provider_reported_usage
        if usage:
            row['input_tokens'] += usage.input_tokens or 0
            row['output_tokens'] += usage.output_tokens or 0
            
        if call.status == 'completed' and call.artifacts and root_dir:
            for artifact in call.artifacts:
                if artifact.endswith('.json'):
                    try:
                        with open(root_dir / artifact) as af:
                            data = json.load(af)
                            row['duration_seconds'] += data.get('duration_seconds', 0.0)
                    except Exception:
                        pass
                        
    entries = []
    for (provider, model), row in sorted(rows.items()):
        entries.append({'provider': provider, 'model': model, **row})
        
    return {'schema_version': 1, 'tts_usage': entries}
