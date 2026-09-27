#!/usr/bin/env python3
"""One physical DeepSeek request for frozen Dialogue segment 0001, with a receipt."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from bookcast.adapters.compatible import CompatibleLLMProvider
from bookcast.content_models import SegmentScript
from bookcast.generation import GenerationConfig
from bookcast.provider_api import ProviderError, ProviderRequestContext
from bookcast.provider_config import ProviderSpec
from bookcast.storage import fingerprint, write_json
from scripts.llm_reasoning_dialogue import candidate_prompt, estimate_cost, fixture, form_metrics, quality_gate

OUTPUT = Path('output/llm-reasoning-ab/dialogue/diagnostics')


def run(candidate: str, output: Path = OUTPUT) -> dict:
    if candidate not in {'baseline', 'reduced', 'focused'}:
        raise ValueError('Unknown diagnostic candidate')
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise RuntimeError('DEEPSEEK_API_KEY unavailable')
    item = fixture()['dialogue'][0]
    prompt = item['baseline_prompt'] if candidate != 'focused' else candidate_prompt(item, 'focused')
    schema = SegmentScript.model_json_schema()
    if fingerprint({'prompt': item['baseline_prompt'], 'schema': schema}) != item['baseline_input_hash']:
        raise RuntimeError('Frozen baseline input mismatch')
    spec = ProviderSpec(name='deepseek', kind='llm', type='openai-compatible', model='deepseek-flash',
                        base_url='https://api.deepseek.com', api_key_env='DEEPSEEK_API_KEY',
                        timeout_seconds=120,
                        generation=GenerationConfig(thinking='disabled') if candidate == 'reduced' else None)
    output.mkdir(parents=True, exist_ok=True)
    dest = output / f'{candidate}-0001.json'
    if dest.exists():
        raise RuntimeError('Diagnostic receipt already exists; no automatic repeat')
    row = {'candidate': candidate, 'segment_id': item['segment_id'], 'status': 'started',
           'input_hash': fingerprint({'prompt': prompt, 'schema': schema}),
           'historical_baseline_input_hash': item['baseline_input_hash'],
           'started_at': datetime.now(timezone.utc).isoformat()}
    write_json(dest, row)
    provider = CompatibleLLMProvider(spec).for_task(item['task'])
    provider.set_context(ProviderRequestContext(
        job_id=None, output_id=None, logical_chunk_id=f'phase19.3b:diagnostic:{candidate}:0001',
        provider=provider.name, model=provider.model, telemetry_path=output / 'physical_requests.jsonl'))
    started = time.perf_counter()
    try:
        # _chat uses the same official wire builder as generate_structured, without its schema retry.
        raw = provider._chat(prompt, schema)
        row['status'] = 'http_200'
        row['usage'] = provider.last_usage.model_dump() if provider.last_usage else None
        row['reported_model'] = provider.reported_model
        try:
            script = SegmentScript.model_validate_json(raw)
            row['schema_valid'] = True
            row['output'] = script.model_dump()
            row['metrics'] = form_metrics(script, item)
            baseline = form_metrics(SegmentScript.model_validate(item['baseline_output']), item)
            row['quality_gate'] = quality_gate(row['metrics'], baseline)
            row['estimated_cost_cny'] = estimate_cost(provider.last_usage, datetime.now(timezone.utc),
                                                      fixture()['cost_snapshot'])
        except Exception:
            row['schema_valid'] = False
    except ProviderError as exc:
        row['status'] = 'http_error' if provider.last_http_error_observation else 'provider_error'
        row['error_kind'] = exc.kind.value
        row['upstream'] = provider.last_http_error_observation
        row['usage'] = None
    row['latency_seconds'] = round(time.perf_counter() - started, 3)
    write_json(dest, row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', choices=['baseline', 'reduced', 'focused'], required=True)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    row = run(args.candidate, args.output)
    print(json.dumps({k: v for k, v in row.items() if k not in {'input_hash', 'historical_baseline_input_hash', 'output'}},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
