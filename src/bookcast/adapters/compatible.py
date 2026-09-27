"""SDK-free chat/completions adapter for remote and local compatible servers."""

import json
import os
import re
import time
from urllib import error, request
from urllib.parse import urlsplit

from pydantic import ValidationError

from ..models import utc_now
from ..provider_api import ErrorKind, ProviderError, ProviderStatus, ProviderCapabilities, ProviderRequestContext, T
from ..provider_config import ProviderSpec, is_loopback
from ..storage import fingerprint
from ..generation import GenerationAudit, ProviderUsage, resolve_generation


QUOTA_CODES = {"insufficient_quota", "quota_exceeded", "quota_exhausted", "credit_balance_exhausted",
               "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}


def _safe_upstream_text(value: object, *, limit: int = 320, prompt: str = '') -> str | None:
    """Keep a small diagnostic phrase, never an echoed prompt or credential."""
    if not isinstance(value, str):
        return None
    value = value[:2048]
    if len(prompt) >= 24 and any(prompt[index:index + 24] in value
                                 for index in range(0, len(prompt) - 23, 12)):
        return '[upstream text omitted: echoed request]'
    for env_name in ('DEEPSEEK_API_KEY',):
        secret = os.environ.get(env_name)
        if secret:
            value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(?i)\bBearer\s+[^\s,;"\']+', 'Bearer [REDACTED]', value)
    value = re.sub(r'(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{12,})\b', '[REDACTED]', value)
    value = re.sub(r'(?i)\b(?:api[_-]?key|token|authorization)\s*[:=]\s*[^\s,;]+', '[REDACTED]', value)
    value = ''.join(ch if 32 <= ord(ch) <= 126 else ' ' for ch in value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value[:limit] if value else None


def _deepseek_error_observation(status: int, body: bytes, headers: object, payload: dict | None) -> dict:
    prompt = ' '.join(str(message.get('content', '')) for message in (payload or {}).get('messages', [])
                      if isinstance(message, dict))
    try:
        parsed = json.loads(body)
        failure = parsed.get('error', {}) if isinstance(parsed, dict) else {}
        if not isinstance(failure, dict):
            failure = {}
    except (ValueError, UnicodeDecodeError):
        failure = {}
    def get_header(name: str) -> object:
        if not hasattr(headers, 'items'):
            return None
        return next((value for key, value in headers.items() if str(key).lower() == name), None)
    code = _safe_upstream_text(failure.get('code') or failure.get('type'), limit=80, prompt=prompt)
    message = _safe_upstream_text(failure.get('message'), prompt=prompt)
    request_id = _safe_upstream_text(get_header('x-request-id') or get_header('request-id')
                                     or failure.get('request_id'), limit=128)
    trace_id = _safe_upstream_text(get_header('x-trace-id') or failure.get('trace_id'), limit=128)
    content_type = _safe_upstream_text(get_header('content-type'), limit=80)
    # The summary is reconstructed from selected fields, never the raw body.
    summary = json.dumps({'code': code, 'message': message}, ensure_ascii=True, separators=(',', ':'))[:2048]
    return {'http_status': status, 'upstream_code': code, 'upstream_message': message,
            'safe_reason': message or code, 'request_id': request_id, 'trace_id': trace_id,
            'content_type': content_type, 'body_summary': summary}


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
        self.context: ProviderRequestContext | None = None
        self.last_http_error_observation: dict | None = None

    def set_context(self, context: ProviderRequestContext | None) -> None:
        self.context = context

    @property
    def cache_key(self) -> str:
        return fingerprint({"type": self.spec.type, "model": self.model, "endpoint": self.spec.base_url})

    def _record_physical(self, context: ProviderRequestContext | None, record: dict) -> None:
        if context is None or not getattr(context, 'telemetry_path', None):
            return
        try:
            context.telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
            fd = os.open(context.telemetry_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                if os.write(fd, encoded) != len(encoded):
                    raise OSError('short telemetry write')
            finally:
                os.close(fd)
        except OSError:
            if context.on_telemetry_degraded:
                try:
                    context.on_telemetry_degraded('PHYSICAL_REQUEST_LOG_UNAVAILABLE')
                except Exception:
                    pass

    def _request(self, route: str, payload: dict | None = None) -> bytes:
        self.last_http_error_observation = None
        started = utc_now()
        t0 = time.perf_counter()
        url = self.spec.base_url.rstrip("/") + "/" + route
        parsed_url = urlsplit(url)
        host = parsed_url.hostname or ""
        port_part = f":{parsed_url.port}" if parsed_url.port else ""
        clean_netloc = f"{host}{port_part}" if host else parsed_url.netloc
        clean_endpoint = parsed_url._replace(netloc=clean_netloc, query="", fragment="").geturl()

        def _log_physical(http_status: int | None, result: str, *, failure: ProviderError | None = None, usage_avail: bool = False):
            record = {
                'job_id': self.context.job_id if self.context else None,
                'output_id': self.context.output_id if self.context else None,
                'logical_chunk_id': self.context.logical_chunk_id if self.context else None,
                'chunk_id': self.context.logical_chunk_id if self.context else None,
                'provider': self.name,
                'model': self.model,
                'endpoint': clean_endpoint,
                'physical_attempt_index': getattr(self.context, 'physical_attempt_index', 0) if self.context else 0,
                'started_at': started,
                'finished_at': utc_now(),
                'latency': round(time.perf_counter() - t0, 3),
                'http_status': http_status,
                'result': result,
                'retry_reason': failure.kind.value if failure and failure.retryable else None,
                'retryable': failure.retryable if failure else False,
                'usage_available': usage_avail,
                'billing_evidence': f"{self.spec.type}_http",
                'error_kind': failure.kind.value if failure else None,
            }
            if self.last_http_error_observation:
                for field in ('safe_reason', 'upstream_code', 'upstream_message', 'request_id', 'trace_id', 'content_type'):
                    record[field] = self.last_http_error_observation[field]
            self._record_physical(self.context, record)

        try:
            headers = {"Content-Type": "application/json"}
            if self.spec.api_key_env:
                key = os.environ.get(self.spec.api_key_env)
                if not key:
                    raise ProviderError(ErrorKind.AUTH)
                headers["Authorization"] = f"Bearer {key}"
            data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            req = request.Request(url, data=data, headers=headers)
            with request.build_opener(NoRedirect()).open(req, timeout=self.spec.timeout_seconds) as response:
                result = response.read(32 * 1024 * 1024 + 1)
                if len(result) > 32 * 1024 * 1024:
                    raise ProviderError(ErrorKind.SCHEMA)
            self.last_status = ProviderStatus(provider=self.name, model=self.model, availability="available")
            usage_avail = False
            if result and result[:1] in (b'{', b'['):
                try:
                    parsed = json.loads(result.decode('utf-8'))
                    usage_avail = bool(parsed.get('usage'))
                except Exception:
                    pass
            _log_physical(200, "succeeded", usage_avail=usage_avail)
            return result
        except error.HTTPError as exc:
            body = exc.read(64 * 1024)
            failure = classify_http(exc.code, body)
            if urlsplit(self.spec.base_url).hostname == 'api.deepseek.com':
                self.last_http_error_observation = _deepseek_error_observation(exc.code, body, exc.headers, payload)
            _log_physical(exc.code, "failed", failure=failure)
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None
        except error.URLError as exc:
            failure = ProviderError(ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE)
            _log_physical(None, "failed", failure=failure)
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None
        except TimeoutError:
            failure = ProviderError(ErrorKind.TIMEOUT)
            _log_physical(None, "failed", failure=failure)
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None
        except ProviderError as exc:
            failure = ProviderError(exc.kind)
            _log_physical(None, "failed", failure=failure)
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise failure from None
        except (OSError, ValueError):
            failure = ProviderError(ErrorKind.INPUT)
            _log_physical(None, "failed", failure=failure)
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
        bound.set_context(self.context)
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
        """Normalize safe JSON-mode drift; cap optional arrays after one failed attempt."""
        try:
            data = json.loads(text, strict=False)
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
                # On the one bounded retry, an optional list may still exceed maxItems.
                # Keep the first candidates; required/nonempty primary lists
                # must be corrected by the model rather than silently shortened.
                if warned_field and (key != warned_field or not field.get('minItems')):
                    limit = field.get('maxItems')
                    if isinstance(limit, int) and limit > 0:
                        val = data.get(key)
                        if isinstance(val, list) and len(val) > limit:
                            data[key] = val[:limit]
                            modified = True
            return json.dumps(data, ensure_ascii=False)
        except (ValueError, TypeError):
            return text

    def _strip_markdown_fence(self, text: str) -> str:
        """Strip markdown code fence wrapper (```json ... ```) and conversational banter safely."""
        cleaned = text.strip()
        # 1. If text is already valid JSON, return immediately
        try:
            val = json.loads(cleaned, strict=False)
            if isinstance(val, (dict, list)):
                return cleaned
        except (ValueError, TypeError):
            pass

        # 2. Extract from markdown code fences ```json ... ```
        if '```' in cleaned:
            import re
            for match in re.finditer(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned, re.IGNORECASE):
                candidate = match.group(1).strip()
                try:
                    val = json.loads(candidate, strict=False)
                    if isinstance(val, (dict, list)):
                        return candidate
                except (ValueError, TypeError):
                    pass

        # 3. Robust bracket-matching to locate top-level JSON object or array
        for i, ch in enumerate(cleaned):
            if ch in ('{', '['):
                closing = '}' if ch == '{' else ']'
                depth = 0
                in_str = False
                escape = False
                for j in range(i, len(cleaned)):
                    c = cleaned[j]
                    if escape:
                        escape = False
                        continue
                    if c == '\\':
                        escape = True
                        continue
                    if c == '"':
                        in_str = not in_str
                        continue
                    if not in_str:
                        if c == ch:
                            depth += 1
                        elif c == closing:
                            depth -= 1
                            if depth == 0:
                                candidate = cleaned[i:j + 1].strip()
                                try:
                                    val = json.loads(candidate, strict=False)
                                    if isinstance(val, (dict, list)):
                                        return candidate
                                except (ValueError, TypeError):
                                    pass
                                break

        # 4. Fallback: simple fence strip if fence was not closed
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
        if urlsplit(self.spec.base_url).hostname != 'api.deepseek.com' or failure.kind != ErrorKind.SCHEMA:
            return False
            
        if failure.validation_reason == 'finish_reason':
            self._schema_retry_guidance = 'The previous response was incomplete due to length limits. Regenerate the full JSON without excessive reasoning.'
            return True
            
        if failure.error_type != 'ValidationError' or not failure.validation_field or not self._last_schema:
            return False

        field_schema = self._get_field_schema(failure.validation_field, self._last_schema)
        reason = failure.validation_reason
        field_name = failure.validation_field
        primary_prop = field_name.split('.')[0]
        
        if reason and reason.startswith('missing_turns:'):
            self._schema_retry_guidance = f'The array {field_name} is missing entries for specific source turns. Include all requested checks. {reason}'
            return True
        elif reason == 'too_long':
            limit = field_schema.get('maxItems') if isinstance(field_schema, dict) else None
            if not isinstance(limit, int) or limit < 1:
                return False
            self._schema_retry_guidance = f'{field_name} must contain at most {limit} items. Regenerate the full JSON with all required fields.'
        elif reason == 'too_short':
            limit = field_schema.get('minItems') if isinstance(field_schema, dict) else None
            if not isinstance(limit, int) or limit < 1:
                return False
            self._schema_retry_guidance = f'{field_name} must contain at least {limit} items. Regenerate the full JSON with all required fields.'
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
            return False

        self._schema_retry_field = primary_prop
        return True
