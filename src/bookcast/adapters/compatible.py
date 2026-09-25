"""SDK-free chat/completions adapter for remote and local compatible servers."""

import json
import os
import re
from urllib import error, request
from urllib.parse import urlsplit

from pydantic import ValidationError

from ..provider_api import ErrorKind, ProviderError, ProviderStatus, ProviderCapabilities, T
from ..provider_config import ProviderSpec, is_loopback
from ..storage import fingerprint
from ..generation import GenerationAudit, ProviderUsage, resolve_generation


QUOTA_CODES = {"insufficient_quota", "quota_exceeded", "quota_exhausted", "credit_balance_exhausted",
               "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}


def schema_failure(exc: Exception) -> ProviderError:
    """Keep only Pydantic field names and error codes, never response values."""
    if isinstance(exc, ValidationError):
        first = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        field = '.'.join(str(part) for part in first['loc'] if isinstance(part, int)
                         or (isinstance(part, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', part)))
        reason = first['type'] if re.fullmatch(r'[a-z_]+', first['type']) else 'invalid'
        return ProviderError(ErrorKind.SCHEMA, error_type='ValidationError',
                             validation_field=field[:160] or '$', validation_reason=reason)
    return ProviderError(ErrorKind.SCHEMA, error_type=type(exc).__name__)


def classify_http(status: int, body: bytes) -> ProviderError:
    try:
        failure = json.loads(body).get("error", {})
        code, kind = failure.get("code"), failure.get("type")
    except (ValueError, AttributeError):
        code, kind = None, None
    if status in {401, 403}:
        return ProviderError(ErrorKind.AUTH)
    if status == 402 or (status == 429 and any(isinstance(value, str) and value in QUOTA_CODES for value in (code, kind))):
        return ProviderError(ErrorKind.QUOTA)
    if status == 429:
        return ProviderError(ErrorKind.RATE_LIMIT)
    if status in {408, 504}:
        return ProviderError(ErrorKind.TIMEOUT)
    if status >= 500:
        return ProviderError(ErrorKind.UNAVAILABLE)
    return ProviderError(ErrorKind.INPUT)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError(ErrorKind.INPUT)


class CompatibleBase:
    def __init__(self, spec: ProviderSpec):
        self.spec, self.name, self.model = spec, spec.name, spec.model
        self.last_status = ProviderStatus(provider=self.name, model=self.model)

    @property
    def cache_key(self) -> str:
        return fingerprint({"type": self.spec.type, "model": self.model, "endpoint": self.spec.base_url})

    def _request(self, route: str, payload: dict | None = None) -> bytes:
        try:
            headers = {"Content-Type": "application/json"}
            if self.spec.api_key_env:
                key = os.environ.get(self.spec.api_key_env)
                if not key:
                    raise ProviderError(ErrorKind.AUTH)
                headers["Authorization"] = f"Bearer {key}"
            url = self.spec.base_url.rstrip("/") + "/" + route
            data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            req = request.Request(url, data=data, headers=headers)
            with request.build_opener(NoRedirect()).open(req, timeout=self.spec.timeout_seconds) as response:
                result = response.read(32 * 1024 * 1024 + 1)
                if len(result) > 32 * 1024 * 1024:
                    raise ProviderError(ErrorKind.SCHEMA)
            self.last_status = ProviderStatus(provider=self.name, model=self.model, availability="available")
            return result
        except error.HTTPError as exc:
            failure = classify_http(exc.code, exc.read(64 * 1024))
        except error.URLError as exc:
            failure = ProviderError(ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE)
        except TimeoutError:
            failure = ProviderError(ErrorKind.TIMEOUT)
        except ProviderError as exc:
            failure = ProviderError(exc.kind)
        except (OSError, ValueError):
            failure = ProviderError(ErrorKind.INPUT)
        self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
        raise failure from None

    def health_check(self) -> ProviderStatus:
        try:
            response = json.loads(self._request("models"))
            if self.model not in [item["id"] for item in response["data"]]:
                raise ProviderError(ErrorKind.INPUT)
        except ProviderError as exc:
            self.last_status = ProviderStatus.from_error(self.name, self.model, exc)
        except (ValueError, KeyError, TypeError):
            self.last_status = ProviderStatus.from_error(self.name, self.model, ProviderError(ErrorKind.SCHEMA))
        return self.last_status


class CompatibleLLMProvider(CompatibleBase):
    def __init__(self, spec: ProviderSpec):
        super().__init__(spec)
        self.generation_audit: GenerationAudit | None = None
        self.last_usage: ProviderUsage | None = None
        self.reported_model: str | None = None
        self.finish_reason: str | None = None
        self._schema_retry_guidance: str | None = None
        self._schema_retry_field: str | None = None
        self._last_schema: dict | None = None
        self._last_raw_response: str | None = None

    def for_task(self, task: str) -> 'CompatibleLLMProvider':
        # A call-scoped view prevents response metadata leaking between attempts.
        bound = type(self)(self.spec)
        if self.spec.generation is not None or self.spec.reasoning_policy is not None:
            bound.generation_audit = resolve_generation(task, self.spec.reasoning_policy, self.spec.generation)
        return bound

    @property
    def cache_key(self) -> str:
        base = super().cache_key
        if self.generation_audit is not None:
            fields = self.generation_audit.options.model_dump(exclude_none=True)
            return fingerprint({'base': base, 'generation': fields}) if fields else base
        if self.spec.generation is not None or self.spec.reasoning_policy is not None:
            return fingerprint({'base': base, 'generation': self.spec.generation.model_dump(exclude_none=True)
                                if self.spec.generation else {}, 'reasoning_policy': self.spec.reasoning_policy})
        return base

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True, structured=True, local=is_loopback(urlsplit(self.spec.base_url).hostname))

    def _chat(self, prompt: str, schema: dict | None = None) -> str:
        self.last_usage, self.reported_model, self.finish_reason = None, None, None
        messages = [{"role": "user", "content": prompt}]
        payload = {"model": self.model, "messages": messages, "stream": False}
        audit = self.generation_audit or resolve_generation('', self.spec.reasoning_policy, self.spec.generation)
        payload.update(audit.options.request_fields())
        if schema:
            instruction = "Return only JSON matching this schema: " + json.dumps(schema)
            if urlsplit(self.spec.base_url).hostname == 'api.deepseek.com':
                # DeepSeek JSON mode guarantees syntax, not conformance to a supplied schema.
                instruction += " Every listed property must have the schema type; use [] for empty arrays, never null. Do not omit required properties."
                limits = {name: field['maxItems'] for name, field in schema.get('properties', {}).items()
                          if isinstance(field, dict) and isinstance(field.get('maxItems'), int)}
                if limits:
                    instruction += ' Array limits (maximum items, never exceed): ' + json.dumps(limits, sort_keys=True) + '.'
                if self._schema_retry_guidance:
                    instruction += ' Previous response failed validation: ' + self._schema_retry_guidance
            self._schema_retry_guidance = None
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

    def _resolve_ref(self, node: dict, schema: dict) -> dict:
        while isinstance(node, dict) and "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            node = schema.get("$defs", {}).get(ref_name, node)
        return node

    def _get_field_schema(self, path: str, schema: dict) -> dict:
        if not isinstance(schema, dict) or not path or path == "$":
            return schema
        parts = path.split(".")
        cur = schema
        for part in parts:
            cur = self._resolve_ref(cur, schema)
            if part.isdigit():
                if isinstance(cur, dict) and "items" in cur:
                    cur = self._resolve_ref(cur["items"], schema)
            elif isinstance(cur, dict) and "properties" in cur and part in cur["properties"]:
                cur = self._resolve_ref(cur["properties"][part], schema)
            else:
                break
        return self._resolve_ref(cur, schema) if isinstance(cur, dict) else {}

    def _normalize_deepseek_json(self, text: str, schema: dict, warned_field: str | None = None) -> str:
        """Safely normalize null arrays, single strings into string arrays, and secondary array clamp."""
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return text
            modified = False
            properties = schema.get('properties', {})
            for key, raw_field in properties.items():
                field = self._resolve_ref(raw_field, schema)
                # 1. Null array normalization (only when minItems is not required)
                if field.get('type') == 'array' and key in data and data[key] is None and not field.get('minItems'):
                    data[key] = []
                    modified = True
                # 2. String to array of strings
                elif field.get('type') == 'array' and isinstance(data.get(key), str) and field.get('items', {}).get('type') == 'string':
                    val = data[key].strip()
                    data[key] = [val] if val else []
                    modified = True
                # 3. Secondary array clamping on retry
                if warned_field and key != warned_field:
                    limit = field.get('maxItems')
                    if isinstance(limit, int) and limit > 0:
                        val = data.get(key)
                        if isinstance(val, list) and len(val) > limit:
                            data[key] = val[:limit]
                            modified = True
            return json.dumps(data, ensure_ascii=False) if modified else text
        except (ValueError, TypeError):
            return text

    def _strip_markdown_fence(self, text: str) -> str:
        """Strip markdown code fence wrapper (```json ... ```) safely for deepseek."""
        cleaned = text.strip()
        if cleaned.startswith('```'):
            first_newline = cleaned.find('\n')
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            else:
                cleaned = cleaned.lstrip('`')
            cleaned = cleaned.strip()
            if cleaned.endswith('```'):
                cleaned = cleaned[:-3].strip()
        return cleaned

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        schema = response_model.model_json_schema()
        self._last_schema = schema
        warned_field = self._schema_retry_field
        self._schema_retry_field = None
        text = self._chat(prompt, schema)
        if urlsplit(self.spec.base_url).hostname == 'api.deepseek.com':
            text = self._strip_markdown_fence(text)
            text = self._normalize_deepseek_json(text, schema, warned_field=warned_field)
        try:
            return response_model.model_validate_json(text)
        except ValidationError as exc:
            self._last_raw_response = text
            failure = schema_failure(exc)
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None

    def prepare_schema_retry(self, failure: ProviderError) -> bool:
        """Allow one journaled bounded correction for DeepSeek schema drift."""
        if (urlsplit(self.spec.base_url).hostname != 'api.deepseek.com'
                or failure.kind != ErrorKind.SCHEMA or failure.error_type != 'ValidationError'
                or not failure.validation_field or not self._last_schema):
            return False

        field_schema = self._get_field_schema(failure.validation_field, self._last_schema)
        reason = failure.validation_reason
        field_name = failure.validation_field
        primary_prop = field_name.split('.')[0]
        self._schema_retry_field = primary_prop

        if reason == 'too_long':
            limit = field_schema.get('maxItems') if isinstance(field_schema, dict) else None
            if not isinstance(limit, int) or limit < 1:
                limit = 6
            self._schema_retry_guidance = f'{field_name} must contain at most {limit} items. Regenerate the full JSON with all required fields.'
        elif reason == 'model_type':
            req = field_schema.get('required') if isinstance(field_schema, dict) else None
            props = list(field_schema.get('properties', {}).keys()) if isinstance(field_schema, dict) else []
            exp = req or props or "an object matching the schema"
            self._schema_retry_guidance = (
                f"Property '{field_name}' must be an object with fields {exp}, not a plain string or invalid type. "
                f"Regenerate the full JSON with all required fields."
            )
        elif reason == 'missing':
            self._schema_retry_guidance = f"Required property '{field_name}' was omitted. Regenerate the full JSON including all required fields."
        elif reason in ('list_type', 'string_type', 'type_error'):
            exp_type = field_schema.get('type', 'valid type') if isinstance(field_schema, dict) else 'valid type'
            self._schema_retry_guidance = f"Property '{field_name}' must have type '{exp_type}'. Regenerate the full JSON with all required fields."
        else:
            self._schema_retry_guidance = f"Property '{field_name}' failed validation ({reason}). Regenerate the full JSON strictly conforming to the schema."

        if getattr(self, '_last_raw_response', None) and isinstance(self._last_raw_response, str):
            snippet = self._last_raw_response.strip()
            if snippet:
                self._schema_retry_guidance += f' Fix structure while preserving content semantics: {snippet[:2000]}'
        return True
