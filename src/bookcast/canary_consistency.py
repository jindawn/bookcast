"""Offline Canary comparison and pricing views over the durable Attempt journal."""

import json

from .cost import _snapshot_config, calculate_cost_summary


def _bucket(calls, snapshot, physical, prefixes):
    selected = [call for call in calls if call.kind == 'llm' and
                any(call.task.startswith(prefix) for prefix in prefixes) and
                call.status in {'completed', 'failed_retryable', 'failed_permanent'}]
    config = _snapshot_config(snapshot)
    costs = calculate_cost_summary(selected, config, pricing_policy=snapshot,
                                   snapshot_complete=config is not None)
    rows = [row for row in costs['llm']['providers'].values() if row['requests']]
    priced = all(row['cost']['status'] in {'actual', 'estimated'} for row in rows)
    usage = [call.provider_reported_usage for call in selected]
    def total(field):
        values = [getattr(item, field, None) for item in usage]
        return sum(values) if all(value is not None for value in values) else None
    ids = {call.task for call in selected}
    latency = [row['latency'] for row in physical if row.get('logical_chunk_id') in ids
               and isinstance(row.get('latency'), (int, float))]
    return {'request_count': len(selected), 'input_tokens': total('input_tokens'),
            'output_tokens': total('output_tokens'), 'reasoning_tokens': total('reasoning_tokens'),
            'latency_seconds': round(sum(latency), 3) if len(latency) == len(selected) else None,
            'estimated_cost': round(sum(row['cost']['amount'] for row in rows), 6) if priced else None,
            'pricing_status': 'known' if priced else 'unknown'}


def canary_summary(manifest, records: list[dict], physical_path) -> dict:
    physical = []
    if physical_path.is_file():
        try:
            physical = [json.loads(line) for line in physical_path.read_text(encoding='utf-8').splitlines()
                        if line.strip()]
        except (OSError, ValueError):
            physical = []
    calls, snapshot = manifest.ai_calls, manifest.cost_snapshot
    qwen = _bucket(calls, snapshot, physical, ('consistency:tier1:',))
    targeted = _bucket(calls, snapshot, physical, ('consistency:tier2:',))
    audit = _bucket(calls, snapshot, physical, ('consistency:audit:',))
    prices = (qwen['estimated_cost'], targeted['estimated_cost'], audit['estimated_cost'])
    production_cost = round(prices[0] + prices[1], 6) if all(p is not None for p in prices[:2]) else None
    experiment_cost = round(production_cost + prices[2], 6) if production_cost is not None and prices[2] is not None else None
    saving = round(prices[2] - production_cost, 6) if production_cost is not None and prices[2] is not None else None
    return {'schema_version': 1, 'direction': 'two_tier_production_full_audit',
            'segments': records, 'audit_status': 'completed' if records and all(
                row['audit_status'] == 'completed' for row in records) else 'indeterminate',
            'potential_false_negative': (any(row['potential_false_negative'] for row in records)
                if records and all(row['potential_false_negative'] is not None for row in records) else None),
            'two_tier_production': {'qwen': qwen, 'targeted_deepseek': targeted},
            'full_audit': audit, 'two_tier_production_cost': production_cost,
            'full_counterfactual_cost': prices[2], 'actual_canary_experiment_cost': experiment_cost,
            'saving_amount': saving,
            'saving_percent': round(100 * saving / prices[2], 2) if saving is not None and prices[2] else None}
