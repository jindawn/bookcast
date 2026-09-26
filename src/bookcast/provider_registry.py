"""Provider registration is the only place that wires concrete adapters."""

from collections.abc import Callable

from .errors import BookCastError
from .provider_api import Provider
from .provider_chain import ProviderChain
from .provider_config import ProviderSpec, ProvidersConfig


class ProviderRegistry:
    def __init__(self):
        self._factories: dict[tuple[str, str], Callable[[ProviderSpec], Provider]] = {}

    def register(self, kind: str, name: str, factory: Callable[[ProviderSpec], Provider]) -> None:
        if (kind, name) in self._factories:
            raise ValueError("Provider type already registered")
        self._factories[kind, name] = factory

    def create(self, spec: ProviderSpec) -> Provider:
        factory = self._factories.get((spec.kind, spec.type))
        if factory is None:
            raise BookCastError(f"未注册 Provider 类型：{spec.kind}/{spec.type}")
        return factory(spec)

    def types(self) -> list[str]:
        return sorted(f"{kind}/{name}" for kind, name in self._factories)

    def chain(self, config: ProvidersConfig, kind: str, selection: str = "auto") -> ProviderChain:
        if kind == 'tts' and config.tts_routing is not None and selection in ('auto', 'standard', 'high', 'high_quality'):
            by_name = {spec.name: spec for spec in config.providers}
            for name in config.tts_priority:
                if name not in by_name or by_name[name].kind != 'tts':
                    raise BookCastError(f"未配置 tts Provider：{name}")
            from .model_routing import RoutedTTSChain
            return RoutedTTSChain([self.create(by_name[name]) for name in config.tts_priority],
                                  config.tts_routing, selection=selection,
                                  failover_on=config.failover_on)

        priorities = config.llm_priority if kind == "llm" else config.tts_priority
        names = priorities if selection == "auto" else [selection]
        by_name = {spec.name: spec for spec in config.providers}
        if any(name not in by_name or by_name[name].kind != kind for name in names):
            raise BookCastError(f"未配置 {kind} Provider：{selection}")
        if kind == 'llm' and selection == 'auto' and config.llm_routing is not None:
            from .model_routing import RoutedLLMChain
            return RoutedLLMChain([self.create(by_name[name]) for name in names], config.llm_routing,
                                  failover_on=config.failover_on)
        return ProviderChain([self.create(by_name[name]) for name in names], failover_on=config.failover_on)


def default_registry() -> ProviderRegistry:
    from .adapters.compatible import CompatibleLLMProvider
    from .adapters.qwen_llm import QwenLLMProvider
    from .adapters.kokoro import KokoroTTSProvider
    from .adapters.gemini import GeminiTTSProvider
    from .adapters.qwen import QwenTTSProvider
    from .adapters.qwen_cloud import QwenCloudTTSProvider
    from .providers import MockLLMProvider, MockTTSProvider

    def mock_factory(provider_class):
        def build(spec):
            provider = provider_class()
            provider.name, provider.model = spec.name, spec.model
            return provider
        return build

    registry = ProviderRegistry()
    registry.register("llm", "mock", mock_factory(MockLLMProvider))
    registry.register("tts", "mock", mock_factory(MockTTSProvider))
    registry.register("llm", "openai-compatible", CompatibleLLMProvider)
    registry.register("llm", "local", CompatibleLLMProvider)
    registry.register("llm", "qwen-llm", QwenLLMProvider)
    registry.register("tts", "kokoro-local", KokoroTTSProvider)
    registry.register("tts", "gemini-tts", GeminiTTSProvider)
    registry.register("tts", "qwen-local", QwenTTSProvider)
    registry.register("tts", "qwen-cloud-tts", QwenCloudTTSProvider)
    return registry
