"""Audio-only acceptance client: fork verified content, delegate all work to Core.

Run with the project Python. A new destination is required; --resume reuses it.
No LLM requests are possible, including when cached content becomes invalid.
"""
import argparse
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from bookcast.errors import BookCastError
from bookcast.composition import settings_snapshot
from bookcast.models import utc_now
from bookcast.pipeline import Pipeline, artifacts_valid, load_manifest
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import ProvidersConfig, load_config
from bookcast.provider_registry import default_registry
from bookcast.storage import artifact_path, job_lock, sha256_file, write_json


class CachedLLM:
    def __init__(self, provider):
        self.provider = provider

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def for_task(self, task):
        method = getattr(self.provider, 'for_task', None)
        return CachedLLM(method(task) if method else self.provider)

    def generate(self, *args, **kwargs):
        raise BookCastError('A/B 仅重渲染音频：内容缓存失效，禁止重新调用 LLM。')

    generate_structured = generate


def prepare(source, destination, tts_config):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise BookCastError('A/B 目标必须是独立且不存在的新目录。')
    with job_lock(source):
        original = load_manifest(source / 'manifest.json')
        if original.status != 'completed' or original.provider_settings is None:
            raise BookCastError('A/B 需要已完成且有配置快照的来源任务。')
        config = ProvidersConfig.model_validate(original.provider_settings['config'])
        config.providers = [s for s in config.providers if s.kind == 'llm'] + [
            s for s in tts_config.providers if s.kind == 'tts']
        config.tts_priority = tts_config.tts_priority
        config = ProvidersConfig.model_validate(config.model_dump())
        manifest = original.model_copy(deep=True)
        manifest.steps = {k: v for k, v in manifest.steps.items()
                          if not k.startswith('tts') and k not in {'merge', 'output'}}
        if any(s.status != 'completed' or not artifacts_valid(source, s) for s in manifest.steps.values()):
            raise BookCastError('来源内容检查点缺失或损坏。')
        manifest.ai_calls = [a for a in manifest.ai_calls if a.kind == 'llm']
        manifest.artifact_records = {k: v for k, v in manifest.artifact_records.items() if v.step in manifest.steps}
        manifest.provider_status = {k: v for k, v in manifest.provider_status.items() if k.startswith('llm:')}
        manifest.job_id, manifest.owner, manifest.status = uuid4().hex, None, 'pending'
        manifest.error, manifest.error_kind, manifest.inventory_complete = None, None, False
        manifest.created_at = manifest.updated_at = utc_now()
        manifest.provider_settings = settings_snapshot(config, original.provider_settings.get('llm_selection', 'auto'), 'auto')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.tts-ab-', dir=destination.parent) as temporary:
            stage = Path(temporary) / 'job'
            stage.mkdir()
            names = {n for s in manifest.steps.values() for n in s.artifacts}
            for name in sorted(names):
                target = artifact_path(stage, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact_path(source, name), target)
            write_json(stage / 'tts-ab-source.json', {
                'source_job_id': original.job_id,
                'source_manifest_sha256': sha256_file(source / 'manifest.json'),
                'imported_llm_attempt_ids': [a.id for a in manifest.ai_calls],
                'scripts': {n: sha256_file(stage / n) for n in sorted(names) if n.startswith('scripts/')},
                'note': 'LLM attempts imported for cache provenance; not new API calls.'})
            write_json(stage / 'manifest.json', manifest.model_dump())
            stage.rename(destination)
    return destination


def render(destination):
    manifest = load_manifest(destination / 'manifest.json')
    config = ProvidersConfig.model_validate(manifest.provider_settings['config'])
    registry = default_registry()
    llm = registry.chain(config, 'llm', manifest.provider_settings.get('llm_selection', 'auto'))
    cached = ProviderChain([CachedLLM(p) for p in llm.providers], failover_on=config.failover_on)
    return Pipeline(cached, registry.chain(config, 'tts'), provider_settings=manifest.provider_settings).resume_job(destination, retry=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        if not args.resume:
            prepare(args.source, args.destination, load_config(args.config))
        elif not (args.destination / 'tts-ab-source.json').is_file():
            parser.error('--resume requires an existing A/B job; saved configuration is used')
        print(render(args.destination))
    except (BookCastError, ValueError, OSError):
        parser.exit(1, 'A/B 未完成：检查目标 manifest 的安全错误分类、配置及内容缓存；不会重新调用 LLM。\n')
