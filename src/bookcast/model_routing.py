"""Task intent selects an existing chain; execution and audit stay in that chain."""

from collections.abc import Sequence

from .models import AIAttempt
from .provider_api import FAILOVER_ERRORS, Provider
from .provider_chain import ProviderChain
from .provider_config import LLMRouting, TTSRouting
from .storage import fingerprint


class RoutedLLMChain(ProviderChain):
    """One sticky fallback state per profile, using the existing Attempt journal.

    The parent provider inventory remains available for D-014 cache validation.
    This class does not add retry loops, transport, or a second persistence layer.
    """

    def __init__(self, providers: Sequence[Provider], routing: LLMRouting, *, failover_on=FAILOVER_ERRORS):
        super().__init__(providers, failover_on=failover_on)
        self.routing = routing.model_copy(deep=True)
        by_name = {provider.name: provider for provider in self.providers}
        if len(by_name) != len(self.providers):
            raise ValueError('Routing requires unique provider names')
        self.routes = {}
        for profile, names in self.routing.chains().items():
            if any(name not in by_name for name in names):
                raise ValueError('Unknown routing provider')
            chain = ProviderChain([by_name[name] for name in names], failover_on=self.failover_on)
            chain.statuses = self.statuses
            self.routes[profile] = chain

    @property
    def cache_key(self) -> str:
        return fingerprint({'providers': super().cache_key,
                            'llm_routing': self.routing.model_dump(mode='json')})

    def restore(self, calls: list[AIAttempt], kind: str) -> None:
        if kind != 'llm':
            raise ValueError('LLM routing cannot restore TTS calls')
        for profile, chain in self.routes.items():
            chain.restore([call for call in calls if call.kind == kind
                           and self.routing.profile_for_task(call.task) == profile], kind)

    def execute(self, *, task: str, kind: str, prompt_version: str, input_hash: str,
                invoke, persist, observe, context=None) -> dict[str, str]:
        if kind != 'llm':
            raise ValueError('LLM routing cannot execute TTS calls')
        chain = self.routes[self.routing.profile_for_task(task)]
        return chain.execute(task=task, kind=kind, prompt_version=prompt_version,
                             input_hash=input_hash, invoke=invoke, persist=persist, observe=observe,
                             context=context)


class RoutedTTSChain(ProviderChain):
    """Episode-level TTS quality routing to a single chosen provider.

    Resolves to a single Provider per episode based on quality profile ('standard' or 'high_quality')
    or explicit override. Mid-stream failover to another provider is strictly forbidden.
    """

    def __init__(self, providers: Sequence[Provider], routing: TTSRouting, selection: str = 'auto', *, failover_on=FAILOVER_ERRORS):
        super().__init__(providers, failover_on=failover_on)
        self.routing = routing.model_copy(deep=True)
        self.selection = selection
        by_name = {p.name: p for p in self.providers}
        target_name = self.routing.resolve(selection)
        if target_name not in by_name:
            raise ValueError(f'TTS routing target provider not found: {target_name}')
        self.selected_provider = by_name[target_name]
        # Active chain contains ONLY the selected single provider:
        # this guarantees NO mid-stream failover to another provider, preserving voice consistency.
        self._active_chain = ProviderChain([self.selected_provider], failover_on=self.failover_on)
        self._active_chain.statuses = self.statuses

    @property
    def cache_key(self) -> str:
        return fingerprint({
            'providers': super().cache_key,
            'tts_routing': self.routing.model_dump(mode='json'),
            'selected': self.selected_provider.name,
            'selection': self.selection,
        })

    @property
    def providers(self) -> list[Provider]:
        # Return the active single provider so validate_tts_chain and speech synthesis
        # inspect only the chosen provider for this episode.
        if hasattr(self, 'selected_provider'):
            return [self.selected_provider]
        return getattr(self, '_all_providers', [])

    @providers.setter
    def providers(self, value: list[Provider]) -> None:
        self._all_providers = value

    def capabilities(self):
        return self.selected_provider.capabilities()

    def restore(self, calls: list[AIAttempt], kind: str) -> None:
        if kind != 'tts':
            raise ValueError('TTS routing cannot restore non-TTS calls')
        self._active_chain.restore(calls, kind)

    def execute(self, *, task: str, kind: str, prompt_version: str, input_hash: str,
                invoke, persist, observe, context=None) -> dict[str, str]:
        if kind != 'tts':
            raise ValueError('TTS routing cannot execute non-TTS calls')
        return self._active_chain.execute(
            task=task, kind=kind, prompt_version=prompt_version,
            input_hash=input_hash, invoke=invoke, persist=persist, observe=observe,
            context=context,
        )
