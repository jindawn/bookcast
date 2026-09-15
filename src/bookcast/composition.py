"""Shared composition for CLI and local clients; Core stays vendor-neutral."""

from .errors import BookCastError
from .pipeline import Pipeline, load_manifest
from .provider_config import load_config, ProvidersConfig
from .provider_registry import default_registry


def settings_snapshot(settings, provider, tts_provider):
    return {'config': settings.model_dump(mode='json'), 'llm_selection': provider, 'tts_selection': tts_provider}


def configured_pipeline(settings, output_dir, provider='auto', tts_provider='auto', *, progress=None):
    registry = default_registry()
    return Pipeline(registry.chain(settings, 'llm', provider), registry.chain(settings, 'tts', tts_provider),
                    output_dir, provider_settings=settings_snapshot(settings, provider, tts_provider), progress=progress)


def resume_pipeline(manifest, root, config, provider, tts_provider, *, progress=None):
    saved = manifest.provider_settings
    if config is not None:
        settings = load_config(config)
    elif saved is not None:
        try:
            settings = ProvidersConfig.model_validate(saved['config'])
        except (KeyError, ValueError):
            raise BookCastError('任务保存的 Provider 配置无效；请显式提供 --config。') from None
    else:
        # Do not use an unrelated CWD bookcast.toml after a reboot or directory change.
        settings = ProvidersConfig(providers=[
            {'name':'mock','kind':'llm','type':'mock','model':'mock-llm-v1'},
            {'name':'mock-tts','kind':'tts','type':'mock','model':'mock-tones-v1'}],
            llm_priority=['mock'],tts_priority=['mock-tts'])
    llm_selection = provider or (saved.get('llm_selection','auto') if saved and config is None else 'auto')
    tts_selection = tts_provider or (saved.get('tts_selection','auto') if saved and config is None else 'auto')
    if not isinstance(llm_selection,str) or not isinstance(tts_selection,str):
        raise BookCastError('任务保存的 Provider 选择无效；请显式提供 --config。')
    registry = default_registry()
    llm, tts = registry.chain(settings,'llm',llm_selection), registry.chain(settings,'tts',tts_selection)
    if saved is None and config is None and {'llm':llm.cache_key,'tts':tts.cache_key} != manifest.config:
        raise BookCastError('旧任务没有可恢复的 Provider 配置；请显式提供 --config，避免误用 Mock 或当前目录配置。')
    return Pipeline(llm,tts,root.parent, provider_settings=settings_snapshot(settings,llm_selection,tts_selection),
                    progress=progress)


def generation_pipeline(source, output_dir, config, provider, tts_provider, resume, *, progress=None):
    if resume and source.is_file():
        from .storage import artifact_path, fingerprint, sha256_file
        book_id = fingerprint({'source_sha256': sha256_file(source), 'format': source.suffix.lower().lstrip('.')})[:24]
        root = artifact_path(output_dir.resolve(), book_id)
        if (root/'manifest.json').is_file():
            return resume_pipeline(load_manifest(root/'manifest.json'), root, config,
                                   None if provider == 'auto' else provider, None if tts_provider == 'auto' else tts_provider, progress=progress)
    settings, registry = load_config(config), default_registry()
    return Pipeline(registry.chain(settings,'llm',provider), registry.chain(settings,'tts',tts_provider), output_dir,
                    provider_settings=settings_snapshot(settings,provider,tts_provider), progress=progress)
