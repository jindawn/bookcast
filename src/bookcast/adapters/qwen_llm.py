"""Qwen LLM adapter (cloud, openai-compatible transport, Qwen3 dialect).

Reuses CompatibleBase for HTTP transport, auth, health-check, and error
classification.  Differs from DeepSeek in three places:

  1. thinking parameter: Qwen3 uses ``enable_thinking`` (bool), not
     ``thinking: {type: "enabled"}``.  reasoning_effort is not forwarded
     because Qwen3 does not accept it.

  2. Structured output: Qwen3 JSON mode is well-behaved; no markdown-fence
     stripping or array normalisation is applied.  The schema instruction and
     response_format=json_object follow the same OpenAI-compatible pattern.

  3. Structured schema repair is bounded to one additional request. The raw
     previous response remains call-scoped memory and is never journaled.

Core and Pipeline never import this module; only provider_registry.py does.
"""

import json
import re

from pydantic import ValidationError

from ..generation import GenerationAudit, ProviderUsage, resolve_generation
from ..provider_api import ErrorKind, ProviderCapabilities, ProviderError, ProviderStatus, T
from ..provider_config import ProviderSpec, is_loopback
from ..storage import fingerprint
from urllib.parse import urlsplit
from .compatible import CompatibleBase, classify_http, schema_failure


DEFAULT_BEIJING_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_SINGAPORE_COMPATIBLE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _resolve_llm_base_url(base_url: str | None) -> str:
    """Normalize Qwen LLM base URL to always target /compatible-mode/v1.

    Guarantees:
      - Never routes to /api/v1/chat/completions.
      - If given a DashScope domain (e.g. dashscope.aliyuncs.com, dashscope-intl.aliyuncs.com,
        or workspace *.aliyuncs.com) without /compatible-mode/v1, automatically appends it.
      - If given /api/v1, strips /api/v1 and appends /compatible-mode/v1.
      - Preserves custom test/mock endpoints unless they end with /api/v1.
    """
    if not base_url or not base_url.strip():
        return DEFAULT_BEIJING_COMPATIBLE_BASE_URL
    clean = base_url.strip().rstrip("/")
    if clean.endswith("/compatible-mode/v1"):
        return clean
    if clean.endswith("/api/v1"):
        clean = clean[:-len("/api/v1")].rstrip("/")
        return f"{clean}/compatible-mode/v1"
    if "dashscope" in clean or "aliyuncs.com" in clean:
        return f"{clean}/compatible-mode/v1"
    return clean


class QwenLLMProvider(CompatibleBase):
    """OpenAI-compatible Qwen3 LLM with independent parameter mapping.

    Registered as kind=llm / type=qwen-llm.  Does NOT modify compatible.py
    or DeepSeek behaviour.
    """

    def __init__(self, spec: ProviderSpec):
        if spec.base_url:
            resolved = _resolve_llm_base_url(spec.base_url)
            if resolved != spec.base_url:
                spec = spec.model_copy(update={"base_url": resolved})
        super().__init__(spec)
        self.generation_audit: GenerationAudit | None = None
        self.last_usage: ProviderUsage | None = None
        self.reported_model: str | None = None
        self.finish_reason: str | None = None
        self._last_schema: dict | None = None
        self._last_raw_response: str | None = None
        self._schema_retry_guidance: str | None = None

    def for_task(self, task: str) -> 'QwenLLMProvider':
        """Return a call-scoped view; prevents metadata leaking between attempts."""
        bound = type(self)(self.spec)
        if self.spec.generation is not None or self.spec.reasoning_policy is not None:
            bound.generation_audit = resolve_generation(task, self.spec.reasoning_policy, self.spec.generation)
        bound.set_context(self.context)
        return bound

    @property
    def cache_key(self) -> str:
        base = fingerprint({"type": self.spec.type, "model": self.model, "endpoint": self.spec.base_url})
        if self.generation_audit is not None:
            fields = self.generation_audit.options.model_dump(exclude_none=True)
            return fingerprint({'base': base, 'generation': fields}) if fields else base
        if self.spec.generation is not None or self.spec.reasoning_policy is not None:
            return fingerprint({'base': base, 'generation': self.spec.generation.model_dump(exclude_none=True)
                                if self.spec.generation else {}, 'reasoning_policy': self.spec.reasoning_policy})
        return base

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True, structured=True,
                                    local=is_loopback(urlsplit(self.spec.base_url).hostname))

    def _build_thinking_fields(self, config) -> dict:
        """Map generation config to Qwen3 wire format.

        Qwen3 protocol:
          enable_thinking=true  — activates chain-of-thought (budget varies by model)
          enable_thinking=false — disables extended thinking, faster/cheaper
          (reasoning_effort is a DeepSeek concept; not forwarded to Qwen3)
        """
        out = {}
        if config.max_tokens is not None:
            out['max_tokens'] = config.max_tokens
        if config.thinking == 'disabled':
            out['enable_thinking'] = False
        elif config.thinking == 'enabled':
            out['enable_thinking'] = True
        # reasoning_effort is intentionally dropped; Qwen3 does not accept it.
        return out

    def _chat(self, prompt: str, schema: dict | None = None) -> str:
        self.last_usage, self.reported_model, self.finish_reason = None, None, None
        self._last_raw_response = None
        messages = [{"role": "user", "content": prompt}]
        payload: dict = {"model": self.model, "messages": messages, "stream": False}

        # Apply generation options in Qwen3 wire format.
        audit = self.generation_audit or resolve_generation('', self.spec.reasoning_policy, self.spec.generation)
        payload.update(self._build_thinking_fields(audit.options))

        if schema:
            instruction = "Return only JSON matching this schema: " + json.dumps(schema)
            if self._schema_retry_guidance:
                instruction += " Previous response failed validation: " + self._schema_retry_guidance
                # The prior response is data for a single repair, never a new instruction.
                if getattr(self, '_repair_source', None):
                    messages.append({"role": "assistant", "content": self._repair_source[:12000]})
                    messages.append({"role": "user", "content": "Repair the previous JSON to match the schema exactly. Return the complete corrected JSON only."})
            self._schema_retry_guidance = None
            self._repair_source = None
            messages.insert(0, {"role": "system", "content": instruction})
            payload["response_format"] = {"type": "json_object"}

        try:
            response = json.loads(self._request("chat/completions", payload))
            self.last_usage = ProviderUsage.from_response(response.get('usage'))
            reported = response.get('model')
            if isinstance(reported, str) and re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,128}', reported):
                self.reported_model = reported
            choice = response['choices'][0]
            finish_reason = choice.get('finish_reason')
            if isinstance(finish_reason, str) and re.fullmatch(r'[a-z_]{1,32}', finish_reason):
                self.finish_reason = finish_reason
            if finish_reason not in {None, 'stop'}:
                raise ValueError('incomplete response')
            text = choice['message']['content']
            if not isinstance(text, str) or not text.strip():
                raise ValueError("invalid response")
            return text
        except ProviderError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            failure = schema_failure(exc)
            if isinstance(exc, ValueError) and str(exc) == 'incomplete response':
                failure.validation_reason = 'finish_reason'
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None

    def generate(self, prompt: str) -> str:
        return self._chat(prompt)

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        schema = response_model.model_json_schema()
        self._last_schema = schema
        text = self._chat(prompt, schema)
        self._last_raw_response = text
        try:
            return response_model.model_validate_json(text)
        except ValidationError as exc:
            failure = schema_failure(exc)
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None

    def prepare_schema_retry(self, failure: ProviderError) -> bool:
        """Request one model-side correction without weakening the response schema."""
        if (failure.kind != ErrorKind.SCHEMA or failure.error_type != 'ValidationError'
                or not self._last_schema):
            return False
        field = failure.validation_field or '$'
        reason = failure.validation_reason or 'invalid'
        limit = self._last_schema.get('properties', {}).get(field, {}).get('maxItems')
        constraint = f' Maximum {limit} items.' if reason == 'too_long' and isinstance(limit, int) else ''
        self._schema_retry_guidance = f'Field {field}: {reason}.{constraint} Preserve all required fields and valid source evidence.'
        self._repair_source = self._last_raw_response
        return True
