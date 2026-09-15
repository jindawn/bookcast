"""Bounded failover over configured providers, with a durable attempt observer."""

from collections.abc import Callable, Sequence
from uuid import uuid4

from .models import AIAttempt, utc_now
from .provider_api import Provider, ProviderError, ProviderStatus, ErrorKind, FAILOVER_ERRORS, classify_error
from .storage import fingerprint


def provider_for_task(provider: Provider, task: str) -> Provider:
    bind = getattr(provider, 'for_task', None)
    return bind(task) if bind else provider


def provider_config_hash(provider: Provider, task: str | None = None) -> str:
    if task is not None:
        provider = provider_for_task(provider, task)
    return fingerprint({'name': provider.name, 'model': provider.model, 'configuration': provider.cache_key})


class ProviderChain:
    def __init__(self, providers: Sequence[Provider], *, failover_on=FAILOVER_ERRORS):
        if not providers or not set(failover_on).issubset(FAILOVER_ERRORS):
            raise ValueError("Provider chain or failover policy invalid")
        identities = [(provider.name, provider.model) for provider in providers]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate provider identity")
        self.providers, self.failover_on = list(providers), frozenset(failover_on)
        self.index = 0
        self.disabled: set[int] = set()
        self.statuses = {p.name: ProviderStatus(provider=p.name, model=p.model) for p in providers}

    @property
    def cache_key(self) -> str:
        return fingerprint([(p.name, p.model, p.cache_key) for p in self.providers])

    def restore(self, calls: list[AIAttempt], kind: str) -> None:
        # Keep the most recently successful/configured provider after restart.
        # A fresh invocation may re-probe formerly unavailable services if needed.
        self.disabled.clear()
        self.index = 0
        for call in reversed(calls):
            if call.kind != kind or call.status not in {"completed", "failed_retryable"}:
                continue
            for index, provider in enumerate(self.providers):
                if ((provider.name, provider.model) == (call.provider, call.model)
                        and (call.provider_config_hash is None or call.provider_config_hash == provider_config_hash(provider, call.task))):
                    self.index = index
                    if call.status == "failed_retryable" and call.error in self.failover_on:
                        self.index = (index + 1) % len(self.providers)
                    return

    def execute(self, *, task: str, kind: str, prompt_version: str, input_hash: str,
                invoke: Callable, persist: Callable, observe: Callable[[AIAttempt], None]) -> dict[str, str]:
        order = list(range(self.index, len(self.providers))) + list(range(self.index))
        last_error = ProviderError(ErrorKind.UNAVAILABLE)
        for index in order:
            if index in self.disabled:
                continue
            provider = provider_for_task(self.providers[index], task)
            attempt = AIAttempt(id=uuid4().hex, task=task, kind=kind, provider=provider.name,
                                model=provider.model, prompt_version=prompt_version, input_hash=input_hash,
                                provider_config_hash=provider_config_hash(provider),
                                generation=getattr(provider, 'generation_audit', None))
            observe(attempt)
            attempt.status, attempt.timestamp = "running", utc_now()
            observe(attempt)
            invoked = False
            try:
                result = invoke(provider)
                invoked = True
                # Validation and durable output happen before completion is recorded.
                artifacts = persist(result)
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                attempt.provider_reported_usage = getattr(provider, 'last_usage', None)
                attempt.reported_model = getattr(provider, 'reported_model', None)
                failure = classify_error(exc)
                if invoked and not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    failure = ProviderError(ErrorKind.BUSINESS)
                attempt.status = "failed_retryable" if failure.retryable else "failed_permanent"
                attempt.error, attempt.retryable, attempt.timestamp = failure.kind.value, failure.retryable, utc_now()
                observe(attempt)
                self.statuses[provider.name] = ProviderStatus.from_error(provider.name, provider.model, failure)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if failure.kind not in self.failover_on:
                    raise failure from None
                self.disabled.add(index)
                last_error = failure
                continue
            attempt.status, attempt.artifacts = "completed", artifacts
            attempt.provider_reported_usage = getattr(provider, 'last_usage', None)
            attempt.reported_model = getattr(provider, 'reported_model', None)
            attempt.output_hash = next(iter(artifacts.values())) if len(artifacts) == 1 else fingerprint(artifacts)
            attempt.timestamp = utc_now()
            observe(attempt)
            self.statuses[provider.name] = ProviderStatus(provider=provider.name, model=provider.model, availability="available")
            self.index = index
            return artifacts
        raise last_error
