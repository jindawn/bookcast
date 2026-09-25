"""Offline content-cost smoke: explicit Mock providers, isolated temporary job."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bookcast.pipeline import Pipeline
from bookcast.providers import MockLLMProvider, MockTTSProvider


def main():
    source = Path(__file__).resolve().parents[1] / 'examples/content-demo.txt'
    with TemporaryDirectory(prefix='bookcast-llm-cost-') as directory:
        job = Pipeline(MockLLMProvider(), MockTTSProvider(), Path(directory)).generate(
            source, mode='two_host', minutes=3)
        usage = json.loads((job / 'usage/llm_usage.json').read_text(encoding='utf-8'))
        plan = json.loads((job / 'plans/episode.json').read_text(encoding='utf-8'))
        print(json.dumps({'provider': 'mock', 'real_api_calls': 0,
                          'llm_request_count': usage['total']['request_count'],
                          'by_stage': {stage: row['request_count'] for stage, row in usage['by_stage'].items()},
                          'selected_segments': len(plan['segments']),
                          'estimated_cost': usage['total']['estimated_cost']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
