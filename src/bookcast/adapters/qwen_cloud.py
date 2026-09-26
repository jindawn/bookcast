"""Qwen Cloud TTS adapter (DashScope Qwen-TTS / CosyVoice API, per-unit synthesis).

Supports:
  - Qwen-TTS series (e.g. qwen3-tts-instruct-flash, qwen3-tts-flash):
    HTTP endpoint: {base_url}/services/aigc/multimodal-generation/generation
    Wire protocol:
      POST {base_url}/services/aigc/multimodal-generation/generation
      Headers: Authorization: Bearer <key>, Content-Type: application/json
      Body: {
        "model": "qwen3-tts-instruct-flash",
        "input": {"text": <text>, "voice": <voice>, "language_type": <lang>},
        "parameters": {"instructions": <style>, "optimize_instructions": <bool>}
      }
      Success: 200 {"output": {"audio": {"url": "<oss-wav-url>", "data": ""}, ...}}
      Audio is downloaded from url (or decoded from Base64 data if streaming).

  - CosyVoice series (e.g. cosyvoice-v2, cosyvoice-v1):
    HTTP endpoint: {base_url}/services/aigc/text2voice/voice-synthesis
    Wire protocol:
      POST {base_url}/services/aigc/text2voice/voice-synthesis
      Headers: Authorization: Bearer <key>, Content-Type: application/json
      Body: {
        "model": "cosyvoice-v2",
        "input": {"text": <text>, "voice": <voice>},
        "parameters": {"sample_rate": 24000, "format": "pcm"}
      }
      Success: 200 {"output": {"audio": "<base64-pcm>"}, ...}

Region endpoints:
  - China Beijing: https://dashscope.aliyuncs.com/api/v1
  - Singapore: https://dashscope-intl.aliyuncs.com/api/v1
  - Workspace-specific endpoints (e.g. https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1)
  Configurable via spec.base_url or qwen_cloud_tts.region.

Audio contract:
  Mono s16le PCM or WAV wrapped in a standard WAV container. Validated via _verify_wav.

DashScope quota codes: Ariel rate-limit codes mapped conservatively.

Not modified: qwen-local (QwenTTSProvider) — independent adapter.
Not modified: CompatibleBase — we import its classify_http and NoRedirect only.
Not modified: Gemini adapter or CloudTTSConfig.
"""

import base64
import binascii
import io
import json
import os
import re
import time
import wave
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit
from pydantic import ValidationError

from ..generation import ProviderUsage
from ..models import utc_now
from ..provider_api import ErrorKind, ProviderCapabilities, ProviderError, ProviderRequestContext, ProviderStatus, SpeechInfo, SpeechUnit
from ..provider_config import is_loopback
from ..storage import fingerprint
from .compatible import NoRedirect

# Official DashScope endpoints:
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_SINGAPORE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
QWEN_TTS_ROUTE = "services/aigc/multimodal-generation/generation"
COSYVOICE_ROUTE = "services/aigc/text2voice/voice-synthesis"
_ENDPOINT = f"{DEFAULT_BASE_URL}/{QWEN_TTS_ROUTE}"
_WIRE_VERSION = "qwen-cloud-tts-v2:multimodal-generation"
_MAX_RESPONSE = 8 * 1024 * 1024  # 8 MB ceiling for audio responses

# DashScope error codes that signal quota exhaustion (permanent).
_QUOTA_CODES = {
    "Ariel.InsufficientBalance",
    "Ariel.QuotaExceeded",
    "Ariel.AccountArrears",
    "QuotaExceeded",
    "InvalidApiKey",
}

# Rate-limit / transient codes.
_RATE_LIMIT_CODES = {
    "Throttling",
    "Throttling.RateQuota",
    "Throttling.AllocationQuota",
    "Ariel.UserRequestRateLimit",
}


def _is_cosyvoice(model: str) -> bool:
    """Return True if model belongs to the legacy CosyVoice family."""
    return model.lower().startswith("cosyvoice")


def _resolve_endpoint(base_url: str | None, model: str, region: str | None = None) -> str:
    """Resolve endpoint URL respecting custom base_url, region, and model protocol.

    Guarantees:
      - Never routes to /compatible-mode/v1/services/...
      - If given /compatible-mode/v1, strips it and routes to /api/v1.
      - Supports Beijing default, Singapore, and workspace-specific endpoints.
    """
    if base_url and base_url.strip():
        clean = base_url.strip().rstrip("/")
    elif region in ("singapore", "intl", "ap-southeast-1"):
        clean = DEFAULT_SINGAPORE_BASE_URL
    else:
        clean = DEFAULT_BASE_URL

    if clean.endswith(f"/{QWEN_TTS_ROUTE}") or clean.endswith(f"/{COSYVOICE_ROUTE}"):
        return clean

    if clean.endswith("/compatible-mode/v1"):
        clean = clean[:-len("/compatible-mode/v1")].rstrip("/")

    if not clean.endswith("/api/v1"):
        clean = f"{clean}/api/v1"

    route = COSYVOICE_ROUTE if _is_cosyvoice(model) else QWEN_TTS_ROUTE
    return f"{clean}/{route}"


def _classify_dashscope(status: int, body: bytes) -> ProviderError:
    """Map DashScope HTTP / JSON error codes to ProviderError.

    Only code strings and numeric HTTP status are examined — never message prose.
    """
    try:
        doc = json.loads(body)
        code = doc.get("code", "") if isinstance(doc, dict) else ""
    except (ValueError, AttributeError):
        code = ""

    if status in {401, 403} or code in {"InvalidApiKey", "AuthenticationFailed"}:
        return ProviderError(ErrorKind.AUTH)
    if code in _QUOTA_CODES or status == 402:
        return ProviderError(ErrorKind.QUOTA)
    if code in _RATE_LIMIT_CODES or status == 429:
        return ProviderError(ErrorKind.RATE_LIMIT)
    if status in {408, 504}:
        return ProviderError(ErrorKind.TIMEOUT)
    if status >= 500:
        return ProviderError(ErrorKind.UNAVAILABLE)
    return ProviderError(ErrorKind.INPUT)


def _decode_pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap raw s16le PCM in a valid WAV container; validate non-empty even length."""
    if len(pcm_bytes) == 0:
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="empty audio response")
    if len(pcm_bytes) % 2 != 0:
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="invalid raw PCM length (odd bytes)")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav_out:
        wav_out.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wav_out.writeframes(pcm_bytes)
    return out.getvalue()


def _verify_wav(wav_bytes: bytes, sample_rate: int) -> None:
    """Assert the WAV is well-formed mono s16le at the expected sample rate."""
    if not wav_bytes.startswith(b"RIFF"):
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="invalid RIFF header")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            if (channels, sampwidth, framerate) != (1, 2, sample_rate):
                raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                    validation_reason=f"unexpected WAV format: ch={channels} sw={sampwidth} rate={framerate}")
            if nframes == 0:
                raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                    validation_reason="audio frames count is 0")
            read = wf.readframes(nframes)
            if len(read) != nframes * sampwidth * channels:
                raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                    validation_reason="audio data is truncated")
    except (wave.Error, EOFError) as exc:
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="invalid WAV container") from exc


def _download_audio_bytes(url: str, timeout: float = 30.0) -> bytes:
    """Download audio file from returned OSS URL; validate non-empty and bounds."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="invalid audio URL")
    if is_loopback(parsed.hostname):
        raise ProviderError(ErrorKind.INPUT, validation_reason="unsafe loopback audio URL")

    class SafeAudioRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urlsplit(newurl)
            if target.scheme not in {"http", "https"} or not target.hostname or is_loopback(target.hostname):
                raise ProviderError(ErrorKind.INPUT, validation_reason="unsafe audio redirect URL")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    req = request.Request(url)
    try:
        with request.build_opener(SafeAudioRedirect()).open(req, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE + 1)
        if len(raw) > _MAX_RESPONSE:
            raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                validation_reason="audio download exceeds size limit")
        if len(raw) == 0:
            raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                validation_reason="empty audio downloaded")
        return raw
    except error.HTTPError as exc:
        raise ProviderError(ErrorKind.UNAVAILABLE, error_type="download_error",
                            validation_reason=f"HTTP {exc.code} downloading audio") from None
    except error.URLError as exc:
        err_kind = ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE
        raise ProviderError(err_kind, error_type="download_error") from None
    except TimeoutError:
        raise ProviderError(ErrorKind.TIMEOUT, error_type="download_error") from None
    except ProviderError:
        raise
    except (OSError, ValueError):
        raise ProviderError(ErrorKind.UNAVAILABLE, error_type="download_error") from None


def _decode_response(body: bytes, sample_rate: int, downloader=None) -> bytes:
    """Extract audio from DashScope response body (URL download or Base64 PCM/WAV)."""
    try:
        doc = json.loads(body)
    except ValueError:
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="response is not JSON")
    if not isinstance(doc, dict):
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="response root is not a dict")

    code = doc.get("code")
    if code and isinstance(code, str) and code != "200" and code != "":
        status = doc.get("status_code", 400)
        raise _classify_dashscope(status if isinstance(status, int) else 400, body)

    output = doc.get("output")
    if not isinstance(output, dict):
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="missing output field")

    raw_audio = output.get("audio")
    if raw_audio is None:
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                            validation_reason="missing audio field in output")

    # Case 1: Legacy / CosyVoice format: output.audio is string of Base64 PCM
    if isinstance(raw_audio, str):
        if not raw_audio.strip():
            raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                validation_reason="missing audio field in output")
        try:
            pcm_bytes = base64.b64decode(raw_audio, validate=True)
        except (binascii.Error, ValueError):
            raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                validation_reason="invalid base64 audio encoding")
        wav_bytes = _decode_pcm_to_wav(pcm_bytes, sample_rate)
        _verify_wav(wav_bytes, sample_rate)
        return wav_bytes

    # Case 2: Qwen-TTS format: output.audio is dict {"url": "...", "data": "..."}
    if isinstance(raw_audio, dict):
        url = raw_audio.get("url")
        data = raw_audio.get("data")
        if isinstance(url, str) and url.strip():
            dl = downloader or _download_audio_bytes
            raw_download = dl(url.strip())
            if raw_download.startswith(b"RIFF"):
                _verify_wav(raw_download, sample_rate)
                return raw_download
            wav_bytes = _decode_pcm_to_wav(raw_download, sample_rate)
            _verify_wav(wav_bytes, sample_rate)
            return wav_bytes
        elif isinstance(data, str) and data.strip():
            try:
                pcm_or_wav = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                    validation_reason="invalid base64 audio encoding")
            if pcm_or_wav.startswith(b"RIFF"):
                _verify_wav(pcm_or_wav, sample_rate)
                return pcm_or_wav
            wav_bytes = _decode_pcm_to_wav(pcm_or_wav, sample_rate)
            _verify_wav(wav_bytes, sample_rate)
            return wav_bytes
        else:
            raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                validation_reason="missing audio field in output")

    raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                        validation_reason="missing audio field in output")


class QwenCloudTTSProvider:
    """Qwen TTS Instruct cloud adapter (DashScope, per-unit synthesis).

    Registered as kind=tts / type=qwen-cloud-tts. Uses speech_units path.
    Each synthesize_unit() call makes exactly one HTTP request; the adapter
    respects min_request_interval to avoid hitting DashScope rate limits.

    Supports Qwen-TTS (qwen3-tts-instruct-flash) via multimodal-generation/generation
    and CosyVoice (cosyvoice-v2) via text2voice/voice-synthesis.
    """

    def __init__(self, spec, *, sleeper=None):
        self.spec = spec
        self.name = spec.name
        self.model = spec.model
        self.settings = spec.qwen_cloud_tts
        self.endpoint = _resolve_endpoint(spec.base_url, spec.model, getattr(self.settings, "region", None))
        self._last_request_at: float = 0.0
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self.last_status = ProviderStatus(provider=self.name, model=self.model)
        self.last_usage = None
        self.reported_model: str | None = None

    @property
    def cache_key(self) -> str:
        return fingerprint({
            "adapter": _WIRE_VERSION,
            "model": self.model,
            "endpoint": self.endpoint,
            "sample_rate": self.settings.sample_rate,
            "host_voice": self.settings.host_voice,
            "guest_voice": self.settings.guest_voice,
            "host_style": self.settings.host_style,
            "guest_style": self.settings.guest_style,
            "instructions": getattr(self.settings, "instructions", None),
            "language_type": getattr(self.settings, "language_type", "Chinese"),
        })

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(speech=True, speech_units=True, cloud=True)

    def health_check(self) -> ProviderStatus:
        """Check that the API key env var is set; no live request made."""
        key = os.environ.get(self.spec.api_key_env or "")
        if not key or not key.strip():
            self.last_status = ProviderStatus.from_error(
                self.name, self.model, ProviderError(ErrorKind.AUTH))
        else:
            self.last_status = ProviderStatus(provider=self.name, model=self.model,
                                              availability="available")
        return self.last_status

    def _rate_limit_wait(self) -> None:
        """Enforce min_request_interval between successive DashScope requests."""
        interval = self.settings.min_request_interval
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < interval:
            self._sleeper(interval - elapsed)

    def _record_physical(self, context: ProviderRequestContext | None, record: dict) -> None:
        if context is None:
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

    def _download_audio(self, url: str) -> bytes:
        return _download_audio_bytes(url, timeout=self.spec.timeout_seconds)

    def _build_payload(self, text: str, voice_id: str, style: str | None = None) -> tuple[dict, str]:
        """Build wire payload according to model family (CosyVoice vs Qwen-TTS)."""
        if _is_cosyvoice(self.model):
            synthesize_text = text
            if style:
                synthesize_text = f"<|system|>{style}<|/system|>{text}"
                if len(synthesize_text) > 600:
                    synthesize_text = text
            payload = {
                "model": self.model,
                "input": {"text": synthesize_text, "voice": voice_id},
                "parameters": {"sample_rate": self.settings.sample_rate, "format": "pcm"},
            }
            return payload, synthesize_text
        else:
            inp: dict = {
                "text": text,
                "voice": voice_id,
            }
            lang = getattr(self.settings, "language_type", "Chinese")
            if lang:
                inp["language_type"] = lang
            payload = {
                "model": self.model,
                "input": inp,
            }
            params: dict = {}
            if style:
                params["instructions"] = style
            if getattr(self.settings, "optimize_instructions", False):
                params["optimize_instructions"] = True
            if params:
                payload["parameters"] = params
            return payload, text

    def _send(self, payload_or_text: dict | str, voice_id: str | None = None,
              context: ProviderRequestContext | None = None) -> bytes:
        """Send one synthesis request; return the raw response body bytes."""
        key = os.environ.get(self.spec.api_key_env or "")
        if not key or not key.strip():
            raise ProviderError(ErrorKind.AUTH)

        if isinstance(payload_or_text, dict):
            payload = payload_or_text
        else:
            payload, _ = self._build_payload(payload_or_text, voice_id or self.settings.host_voice)

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-DashScope-DataInspection": "enable",
        }
        req = request.Request(self.endpoint, data=data, headers=headers)

        self._rate_limit_wait()
        started = utc_now()
        t0 = time.perf_counter()
        self._last_request_at = time.monotonic()
        parsed_url = urlsplit(self.endpoint)
        host = parsed_url.hostname or ""
        port_part = f":{parsed_url.port}" if parsed_url.port else ""
        clean_netloc = f"{host}{port_part}" if host else parsed_url.netloc
        clean_endpoint = parsed_url._replace(netloc=clean_netloc, query="", fragment="").geturl()
        attempt_idx = getattr(context, 'physical_attempt_index', 0) if context else 0

        try:
            with request.build_opener(NoRedirect()).open(req, timeout=self.spec.timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE + 1)
            finished = utc_now()
            latency = round(time.perf_counter() - t0, 3)
            if len(raw) > _MAX_RESPONSE:
                self._record_physical(context, {
                    'job_id': context.job_id if context else None,
                    'output_id': context.output_id if context else None,
                    'logical_chunk_id': context.logical_chunk_id if context else None,
                    'chunk_id': context.logical_chunk_id if context else None,
                    'provider': self.name, 'model': self.model,
                    'endpoint': clean_endpoint,
                    'physical_attempt_index': attempt_idx,
                    'started_at': started, 'finished_at': finished,
                    'latency': latency,
                    'http_status': 200, 'result': 'failed',
                    'retry_reason': None, 'retryable': False,
                    'usage_available': False, 'billing_evidence': 'dashscope_tts',
                    'error_kind': 'schema_error',
                })
                raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error",
                                    validation_reason="response exceeds size limit")

            characters = None
            if raw and raw[:1] in (b'{', b'['):
                try:
                    data = json.loads(raw.decode('utf-8'))
                    chars = data.get('usage', {}).get('characters')
                    if isinstance(chars, int) and chars > 0:
                        characters = chars
                except Exception:
                    pass

            rec = {
                'job_id': context.job_id if context else None,
                'output_id': context.output_id if context else None,
                'logical_chunk_id': context.logical_chunk_id if context else None,
                'chunk_id': context.logical_chunk_id if context else None,
                'provider': self.name, 'model': self.model,
                'endpoint': clean_endpoint,
                'physical_attempt_index': attempt_idx,
                'started_at': started, 'finished_at': finished,
                'latency': latency,
                'http_status': 200, 'result': 'succeeded',
                'retry_reason': None, 'retryable': False,
                'usage_available': True, 'billing_evidence': 'dashscope_tts',
                'error_kind': None,
            }
            if characters is not None:
                rec['characters'] = characters
            self._record_physical(context, rec)
            return raw
        except error.HTTPError as exc:
            finished = utc_now()
            latency = round(time.perf_counter() - t0, 3)
            body = exc.read(64 * 1024)
            classified = _classify_dashscope(exc.code, body)
            self._record_physical(context, {
                'job_id': context.job_id if context else None,
                'output_id': context.output_id if context else None,
                'logical_chunk_id': context.logical_chunk_id if context else None,
                'chunk_id': context.logical_chunk_id if context else None,
                'provider': self.name, 'model': self.model,
                'endpoint': clean_endpoint,
                'physical_attempt_index': attempt_idx,
                'started_at': started, 'finished_at': finished,
                'latency': latency,
                'http_status': exc.code, 'result': 'failed',
                'retry_reason': classified.kind.value if classified.retryable else None,
                'retryable': classified.retryable,
                'usage_available': False, 'billing_evidence': 'dashscope_tts',
                'error_kind': classified.kind.value,
            })
            raise classified from None
        except error.URLError as exc:
            finished = utc_now()
            latency = round(time.perf_counter() - t0, 3)
            err_kind = ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE
            retryable = err_kind in (ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE)
            self._record_physical(context, {
                'job_id': context.job_id if context else None,
                'output_id': context.output_id if context else None,
                'logical_chunk_id': context.logical_chunk_id if context else None,
                'chunk_id': context.logical_chunk_id if context else None,
                'provider': self.name, 'model': self.model,
                'endpoint': clean_endpoint,
                'physical_attempt_index': attempt_idx,
                'started_at': started, 'finished_at': finished,
                'latency': latency,
                'http_status': None, 'result': 'failed',
                'retry_reason': err_kind.value if retryable else None,
                'retryable': retryable,
                'usage_available': False, 'billing_evidence': 'dashscope_tts',
                'error_kind': err_kind.value,
            })
            raise ProviderError(err_kind) from None
        except TimeoutError:
            finished = utc_now()
            latency = round(time.perf_counter() - t0, 3)
            self._record_physical(context, {
                'job_id': context.job_id if context else None,
                'output_id': context.output_id if context else None,
                'logical_chunk_id': context.logical_chunk_id if context else None,
                'chunk_id': context.logical_chunk_id if context else None,
                'provider': self.name, 'model': self.model,
                'endpoint': clean_endpoint,
                'physical_attempt_index': attempt_idx,
                'started_at': started, 'finished_at': finished,
                'latency': latency,
                'http_status': None, 'result': 'failed',
                'retry_reason': 'timeout', 'retryable': True,
                'usage_available': False, 'billing_evidence': 'dashscope_tts',
                'error_kind': 'timeout',
            })
            raise ProviderError(ErrorKind.TIMEOUT) from None
        except ProviderError:
            raise
        except (OSError, ValueError):
            finished = utc_now()
            latency = round(time.perf_counter() - t0, 3)
            self._record_physical(context, {
                'job_id': context.job_id if context else None,
                'output_id': context.output_id if context else None,
                'logical_chunk_id': context.logical_chunk_id if context else None,
                'chunk_id': context.logical_chunk_id if context else None,
                'provider': self.name, 'model': self.model,
                'endpoint': clean_endpoint,
                'physical_attempt_index': attempt_idx,
                'started_at': started, 'finished_at': finished,
                'latency': latency,
                'http_status': None, 'result': 'failed',
                'retry_reason': 'temporary_unavailable', 'retryable': True,
                'usage_available': False, 'billing_evidence': 'dashscope_tts',
                'error_kind': 'temporary_unavailable',
            })
            raise ProviderError(ErrorKind.UNAVAILABLE) from None

    def synthesize_unit(self, unit: SpeechUnit, destination: Path) -> SpeechInfo:
        return self.synthesize_unit_with_context(unit, destination, context=None)

    def synthesize_unit_with_context(self, unit: SpeechUnit, destination: Path,
                                     context: ProviderRequestContext | None = None) -> SpeechInfo:
        """Synthesize one bounded unit (≤ 80 chars). One HTTP request per call.

        voice selection: host_voice for 主持人, guest_voice for 嘉宾.
        style selection: host_style/guest_style or instructions.
        """
        from ..speech import normalize_tts_text

        try:
            unit = SpeechUnit.model_validate(unit)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProviderError(ErrorKind.INPUT,
                                validation_reason="invalid speech unit") from exc

        text = normalize_tts_text(unit.text)
        if not text.strip():
            raise ProviderError(ErrorKind.INPUT,
                                validation_reason="empty text after normalization")
        if len(text) > 80:
            raise ProviderError(ErrorKind.INPUT,
                                validation_reason="unit text exceeds 80-char limit after normalization")

        voice_id = (self.settings.host_voice if unit.speaker == "主持人"
                    else self.settings.guest_voice)
        style = (self.settings.host_style if unit.speaker == "主持人"
                 else self.settings.guest_style)
        if not style and getattr(self.settings, "instructions", None):
            style = self.settings.instructions

        payload, _ = self._build_payload(text, voice_id, style)

        try:
            raw = self._send(payload, context=context)
        except ProviderError as exc:
            self.last_status = ProviderStatus.from_error(self.name, self.model, exc)
            raise

        try:
            wav_bytes = _decode_response(raw, self.settings.sample_rate, downloader=self._download_audio)
        except ProviderError as exc:
            self.last_status = ProviderStatus.from_error(self.name, self.model, exc)
            raise

        chars = len(text)
        if isinstance(raw, (bytes, bytearray)) and raw[:1] in (b'{', b'['):
            try:
                data = json.loads(raw.decode('utf-8'))
                server_chars = data.get('usage', {}).get('characters')
                if isinstance(server_chars, int) and server_chars > 0:
                    chars = server_chars
            except Exception:
                pass
        self.last_usage = ProviderUsage(input_tokens=chars, output_tokens=0)

        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(wav_bytes)

        self.last_status = ProviderStatus(provider=self.name, model=self.model,
                                          availability="available")
        return SpeechInfo(audio_kind="speech", voice=voice_id)

    def synthesize(self, script, destination: Path) -> None:
        """Not supported — use synthesize_unit via Core speech pipeline."""
        raise ProviderError(ErrorKind.INPUT,
                            validation_reason="synthesize not supported; use synthesize_unit")
