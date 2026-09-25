"""Per-job cost views derived from saved configuration and the Attempt journal."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .errors import BookCastError
from .generation import task_type
from .provider_config import ProvidersConfig
from .storage import artifact_path, fingerprint, sha256_file, write_json

POLICY = {'timezone': 'Asia/Shanghai', 'pricing_policy': 'cn-workday-peak-v1'}
SUMMARY_VERSION = 2
TERMINAL = {'completed', 'failed_retryable', 'failed_permanent'}


def make_cost_snapshot(provider_settings: dict | None) -> dict | None:
    """Freeze the creation-time, credential-free provider and pricing settings."""
    if not isinstance(provider_settings, dict):
        return None
    try:
        config = ProvidersConfig.model_validate(provider_settings['config'])
    except (KeyError, ValueError, TypeError):
        return None
    return {'config': config.model_dump(mode='json'), **POLICY}


def _snapshot_config(snapshot: dict | None) -> ProvidersConfig | None:
    if not isinstance(snapshot, dict) or any(snapshot.get(key) != value for key, value in POLICY.items()):
        return None
    try:
        return ProvidersConfig.model_validate(snapshot['config'])
    except (KeyError, ValueError, TypeError):
        return None


def _off_peak(timestamp: str | None, policy: dict) -> bool | None:
    if any(policy.get(key) != value for key, value in POLICY.items()) or not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return None
        bj = dt.astimezone(timezone(timedelta(hours=8)))
    except (TypeError, ValueError, OverflowError):
        return None
    return not (bj.weekday() < 5 and (9 <= bj.hour < 12 or 14 <= bj.hour < 18))


def _row(provider: str, model: str, *, tts: bool = False) -> dict:
    row = {'provider': provider, 'model': model}
    if tts:
        row.update(request_count=0, retry_count=0, audio_duration_seconds=0.0,
                   usage_available=False, cost={'amount': 0.0, 'status': 'unavailable'})
    else:
        row.update(requests=0, usage={'cached_input_tokens': 0, 'uncached_input_tokens': 0,
                                      'output_tokens': 0, 'reasoning_tokens': 0},
                   cost={'amount': 0.0, 'status': 'actual'}, by_stage={})
    return row


def _group(provider: str, model: str) -> str:
    return f'{provider}::{model}'


def _worse(current: str, incoming: str) -> str:
    order = {'actual': 0, 'estimated': 1, 'partial': 2, 'unavailable': 3}
    return max((current, incoming), key=order.__getitem__)


def _selected(settings: dict | None, config: ProvidersConfig | None, kind: str) -> tuple[str | None, str | None]:
    if isinstance(settings, dict) and isinstance(settings.get('config'), dict):
        try:
            config = ProvidersConfig.model_validate(settings['config'])
        except ValueError:
            pass
    if config is None:
        return None, None
    settings = settings if isinstance(settings, dict) else {}
    selection = settings.get(f'{kind}_selection', 'auto')
    names = config.llm_priority if kind == 'llm' else config.tts_priority
    name = names[0] if selection == 'auto' and names else selection if isinstance(selection, str) else None
    spec = next((item for item in config.providers if item.name == name), None)
    return name, spec.model if spec else None


def calculate_cost_summary(calls: list, config: ProvidersConfig | None,
                           root_dir: Path | None = None, current_settings: dict | None = None,
                           manifest_info: dict | None = None, *, pricing_policy: dict | None = None,
                           snapshot_complete: bool = True) -> dict:
    """Count every terminal Attempt; only fully priced requests count as actual."""
    policy = pricing_policy or POLICY
    llm_name, llm_model = _selected(current_settings, config, 'llm')
    tts_name, tts_model = _selected(current_settings, config, 'tts')
    summary = {
        'schema_version': SUMMARY_VERSION,
        'job_identity': manifest_info or {},
        'currency': 'CNY',
        'pricing_policy': policy if snapshot_complete else None,
        'llm': {'current_provider': llm_name,
                'current_provider_model': _group(llm_name, llm_model) if llm_name and llm_model else None,
                'providers': {}},
        'tts': {'current_provider': tts_name,
                'current_provider_model': _group(tts_name, tts_model) if tts_name and tts_model else None,
                'providers': {}},
        'total': {'known_amount': 0.0, 'status': 'partial'},
        'diagnostics': [],
    }
    stage_retries = defaultdict(int)
    stage_requests = defaultdict(int)
    known = 0.0
    any_unknown = False
    llm_calls = 0
    for call in calls:
        if call.kind != 'llm' or call.status not in TERMINAL:
            continue
        llm_calls += 1
        stage = task_type(call.task)
        stage_requests[stage] += 1
        if call.status != 'completed':
            stage_retries[stage] += 1
        key = _group(call.provider, call.model)
        row = summary['llm']['providers'].setdefault(key, _row(call.provider, call.model))
        row['requests'] += 1
        part = row['by_stage'].setdefault(stage, {'requests': 0, 'usage': {name: 0 for name in row['usage']},
                                                 'cost': {'amount': 0.0, 'status': 'actual'}})
        part['requests'] += 1
        usage = call.provider_reported_usage
        if usage is not None:
            for target, source in (('cached_input_tokens', 'cache_hit_tokens'),
                                   ('output_tokens', 'output_tokens'), ('reasoning_tokens', 'reasoning_tokens')):
                value = getattr(usage, source, None)
                if value is not None:
                    row['usage'][target] += value
                    part['usage'][target] += value
            if usage.input_tokens is not None and usage.cache_hit_tokens is not None:
                uncached = max(0, usage.input_tokens - usage.cache_hit_tokens)
                row['usage']['uncached_input_tokens'] += uncached
                part['usage']['uncached_input_tokens'] += uncached
        status = 'actual'
        price = None
        if not snapshot_complete or config is None:
            status = 'unavailable'
        elif usage is None or any(getattr(usage, field) is None for field in
                                  ('input_tokens', 'output_tokens', 'cache_hit_tokens')):
            status = 'partial'
        elif usage.cache_hit_tokens > usage.input_tokens:
            status = 'partial'
        else:
            tier = config.pricing.get(call.model)
            off_peak = _off_peak(call.timestamp, policy)
            if tier is None:
                status = 'unavailable'
            elif off_peak is None:
                price = tier.default
                status = 'estimated' if price is not None else 'unavailable'
            else:
                price = tier.off_peak if off_peak else tier.peak
                price = price or tier.default
                if price is None:
                    status = 'unavailable'
        amount = 0.0
        if price is not None and usage is not None:
            amount = ((usage.cache_hit_tokens * price.cached_input_per_million)
                      + ((usage.input_tokens - usage.cache_hit_tokens) * price.uncached_input_per_million)
                      + (usage.output_tokens * price.output_per_million)) / 1_000_000
            known += amount
        else:
            any_unknown = True
        row['cost']['amount'] += amount
        part['cost']['amount'] += amount
        row['cost']['status'] = _worse(row['cost']['status'], status)
        part['cost']['status'] = _worse(part['cost']['status'], status)

    tts_audio = set()
    for call in calls:
        if call.kind != 'tts' or call.status not in TERMINAL:
            continue
        key = _group(call.provider, call.model)
        row = summary['tts']['providers'].setdefault(key, _row(call.provider, call.model, tts=True))
        row['request_count'] += 1
        row['retry_count'] += call.status != 'completed'
        usage = call.provider_reported_usage
        if usage is not None and (usage.input_tokens is not None or usage.output_tokens is not None):
            row['usage_available'] = True
        if call.status == 'completed' and call.artifacts and root_dir:
            for artifact in call.artifacts:
                if artifact.endswith('.json') and artifact not in tts_audio:
                    tts_audio.add(artifact)
                    try:
                        data = json.loads(artifact_path(root_dir, artifact).read_text(encoding='utf-8'))
                        row['audio_duration_seconds'] += data.get('duration_seconds', 0.0)
                    except (OSError, ValueError, TypeError, BookCastError):
                        pass
    if llm_name and llm_model:
        current_key = _group(llm_name, llm_model)
        if current_key not in summary['llm']['providers']:
            row = _row(llm_name, llm_model)
            row['cost']['status'] = 'unavailable'
            summary['llm']['providers'][current_key] = row
    if tts_name and tts_model:
        summary['tts']['providers'].setdefault(_group(tts_name, tts_model), _row(tts_name, tts_model, tts=True))
    for row in summary['llm']['providers'].values():
        row['cost']['amount'] = round(row['cost']['amount'], 4)
        for part in row['by_stage'].values():
            part['cost']['amount'] = round(part['cost']['amount'], 4)
    summary['total']['known_amount'] = round(known, 4)
    summary['total']['status'] = ('unavailable' if llm_calls and not known and any_unknown and
                                  all(row['cost']['status'] == 'unavailable'
                                      for row in summary['llm']['providers'].values()) else
                                  'partial' if any_unknown or summary['tts']['providers'] else 'actual')
    for stage, retries in stage_retries.items():
        if retries > 3 and retries / max(1, stage_requests[stage]) > 0.2:
            summary['diagnostics'].append(f'成本异常提示：本次 {stage} 阶段重试 {retries} 次，可能增加额外费用。')
    if not snapshot_complete:
        summary['diagnostics'].append('任务缺少创建时的完整计价快照；成本不可用。')
    return summary


def job_identity(root: Path, manifest) -> dict:
    """Reject a moved or mismatched job before writing or exposing a summary."""
    output_id = manifest.output_id or manifest.book_id
    if not manifest.job_id or not output_id or root.name != output_id or not manifest.source_sha256:
        raise BookCastError('任务成本身份无法验证；未写入成本摘要。')
    source = artifact_path(root, f'source/input.{manifest.source_format}')
    if not source.is_file() or sha256_file(source) != manifest.source_sha256:
        raise BookCastError('任务来源哈希无法验证；未写入成本摘要。')
    return {'job_id': manifest.job_id, 'output_id': output_id,
            'source_hash': manifest.source_sha256,
            'snapshot_hash': fingerprint(manifest.cost_snapshot),
            'selection_hash': fingerprint(manifest.provider_settings),
            'journal_hash': fingerprint([call.model_dump(mode='json') for call in manifest.ai_calls
                                         if call.status in TERMINAL]),
            'usage_source': {'llm_usage_path': str(artifact_path(root, 'usage/llm_usage.json')),
                             'tts_usage_path': str(artifact_path(root, 'usage/tts_usage.json'))}}


def read_valid_cost_summary(root: Path, manifest) -> dict | None:
    try:
        identity = job_identity(root, manifest)
        value = json.loads(artifact_path(root, 'usage/cost_summary.json').read_text(encoding='utf-8'))
        if (value.get('schema_version') != SUMMARY_VERSION or value.get('job_identity') != identity
                or not isinstance(value.get('llm', {}).get('providers'), dict)):
            return None
        return value
    except (BookCastError, OSError, ValueError, TypeError, AttributeError):
        return None


def refresh_cost_summary(root: Path, manifest) -> dict:
    """Read this job's usage views and atomically replace only its verified summary."""
    identity = job_identity(root, manifest)
    diagnostics = []
    for name in ('llm_usage.json', 'tts_usage.json'):
        try:
            value = json.loads(artifact_path(root, f'usage/{name}').read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('invalid usage view')
        except (OSError, ValueError, BookCastError):
            diagnostics.append(f'{name} 缺失或无效；按任务 Attempt 记录计算。')
    config = _snapshot_config(manifest.cost_snapshot)
    summary = calculate_cost_summary(manifest.ai_calls, config, root, manifest.provider_settings,
                                     identity, pricing_policy=manifest.cost_snapshot if config else None,
                                     snapshot_complete=config is not None)
    summary['diagnostics'].extend(diagnostics)
    write_json(artifact_path(root, 'usage/cost_summary.json'), summary)
    return summary
