"""Optional official Gemini Interactions REST TTS, with no SDK retries."""
import base64
import binascii
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import random
import re
import struct
import sys
import time
from collections.abc import Callable
from urllib import error, request
import wave

from ..generation import ProviderUsage
from ..provider_api import (ErrorKind, ProviderCapabilities, ProviderError, ProviderStatus,
                            SpeechSegment, SegmentSpeechInfo)
from ..storage import fingerprint

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
WIRE_VERSION = "gemini-generate-content-tts-v1:pcm-s16le-24k"
MAX_RESPONSE = 32 * 1024 * 1024

def sanitize_gemini_error(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r'AIza[0-9A-Za-z-_]{35}', '[REDACTED]', text)
    text = re.sub(r'(Bearer\s+)[A-Za-z0-9\-\._~\+/]+', r'\1[REDACTED]', text)
    text = re.sub(r'(x-goog-api-key:?\s*)[A-Za-z0-9\-_]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?:[A-Za-z0-9+/]{4}){10,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?', '[REDACTED]', text)
    if 'transcript' in text.lower() or len(text) > 200:
        text = text[:100] + '... [REDACTED]'
    return text

def classify_http(status, body, headers=None):
    """Use structured status/reasons/quota IDs, never persist or match error prose."""
    try:
        failure = json.loads(body).get('error', {})
        details = failure.get('details', [])
        details = [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []
        reasons = {d.get('reason') for d in details if isinstance(d.get('reason'), str)}
    except (ValueError, AttributeError, TypeError):
        failure, details, reasons = {}, [], set()
        
    error_status = failure.get('status', str(status))
    # DO NOT print or persist raw message to prevent leaking transcripts or keys.
        
    retry_after = None
    if headers and 'Retry-After' in headers:
        try:
            retry_after = int(headers['Retry-After'])
        except ValueError:
            pass

    kind = ErrorKind.BUSINESS
    if status == 401 or failure.get('status') == 'UNAUTHENTICATED' or reasons & {'API_KEY_INVALID', 'API_KEY_EXPIRED'}:
        kind = ErrorKind.AUTH
    elif status == 403:
        kind = ErrorKind.PERMISSION
    elif status == 402 or reasons & {'BILLING_DISABLED', 'BILLING_NOT_ACTIVE', 'QUOTA_EXCEEDED'}:
        kind = ErrorKind.QUOTA
    elif status == 429:
        kind = ErrorKind.RATE_LIMIT
        violations = [v for d in details for v in (d.get('violations') if isinstance(d.get('violations'), list) else [])
                      if isinstance(v, dict)]
        for violation in violations:
            quota_id = str(violation.get('quotaId', '')).lower()
            if ('perday' in quota_id or 'per_day' in quota_id
                    or violation.get('quotaValue') in (0, '0')):
                kind = ErrorKind.QUOTA
        
        # Determine exact 429 reason
        if error_status == 'RESOURCE_EXHAUSTED':
            error_status = 'quota_exceeded' if kind == ErrorKind.QUOTA else 'rate_limit_exceeded'
            if 'too_many_requests' in failure.get('message', '').lower():
                error_status = 'too_many_requests'
    elif status in {408, 504}:
        kind = ErrorKind.TIMEOUT
    elif status >= 500:
        kind = ErrorKind.UNAVAILABLE
    else:
        kind = ErrorKind.SCHEMA

    err = ProviderError(kind, error_type=error_status)
    err.retry_after = retry_after
    return err



class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError(ErrorKind.INPUT)


class GeminiTTSProvider:
    _last_request_time = 0.0
    def __init__(self, spec, *, clock: Callable[[], float] | None = None,
                 sleeper: Callable[[float], None] | None = None):
        self.spec, self.name, self.model = spec, spec.name, spec.model
        self.clock = clock if clock is not None else time.time
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.settings = spec.cloud_tts
        self.last_status = ProviderStatus(provider=self.name, model=self.model)
        self.last_usage = None
        self.reported_model = None

    def capabilities(self):
        return ProviderCapabilities(speech=True, speech_segments=True, multi_speaker=True, cloud=True)

    @property
    def cache_key(self):
        return fingerprint({'adapter': WIRE_VERSION, 'model': self.model, 'endpoint': ENDPOINT,
                            'settings': self.settings.model_dump()})

    def _request(self, payload=None, destination=None):
        from ..models import utc_now
        key = os.environ.get(self.spec.api_key_env)
        if not key or not key.strip():
            raise ProviderError(ErrorKind.AUTH)
        url = ENDPOINT + self.model if payload is None else 'https://generativelanguage.googleapis.com/v1beta/interactions'
        
        attempts = 0
        backoffs = [10, 20, 40, 60]
        
        chunk_id = destination.stem if destination else "unknown"
        physical_log = destination.parents[3] / 'usage' / 'physical_requests.jsonl' if destination else None
        
        def log_physical(status, result_status, retry_reason, usage_avail, billing, started, finished):
            if physical_log:
                physical_log.parent.mkdir(parents=True, exist_ok=True)
                with physical_log.open('a', encoding='utf-8') as pf:
                    import json
                    pf.write(json.dumps({
                        "chunk_id": chunk_id,
                        "physical_attempt_index": attempts,
                        "started_at": started,
                        "finished_at": finished,
                        "http_status": status if status is None else (int(status) if isinstance(status, int) else (int(str(status)) if str(status).isdigit() else 200)),
                        "result": result_status,
                        "retry_reason": retry_reason,
                        "usage_available": usage_avail,
                        "billing_status": billing
                    }) + '\n')

        while True:
            # Active RPM throttling
            now = self.clock()
            interval = getattr(self.settings, 'min_request_interval', 25)
            if payload is not None and now - GeminiTTSProvider._last_request_time < interval:
                sleep_time = interval - (now - GeminiTTSProvider._last_request_time)
                self.sleeper(sleep_time)
            
            started_at = utc_now()
            http_status = None
            usage_available = False
            
            try:
                if payload is not None:
                    GeminiTTSProvider._last_request_time = self.clock()
                    
                req = request.Request(url, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                    data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
                with request.build_opener(NoRedirect()).open(req, timeout=self.spec.timeout_seconds) as response:
                    http_status = getattr(response, 'getcode', lambda: 200)()
                    raw = response.read(MAX_RESPONSE + 1)
                
                finished_at = utc_now()
                if len(raw) > MAX_RESPONSE:
                    raise ProviderError(ErrorKind.SCHEMA)
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ProviderError(ErrorKind.SCHEMA)
                
                # Check for usage
                usage_metadata = value.get('usage_metadata') or value.get('usageMetadata')
                if isinstance(usage_metadata, dict):
                    usage_available = True
                    
                log_physical(http_status, "success", None, usage_available, "billed", started_at, finished_at)
                return value
            except error.HTTPError as exc:
                finished_at = utc_now()
                http_status = exc.code
                failure = classify_http(exc.code, exc.read(64 * 1024), exc.headers)
            except error.URLError as exc:
                finished_at = utc_now()
                failure = ProviderError(ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE)
            except TimeoutError:
                finished_at = utc_now()
                failure = ProviderError(ErrorKind.TIMEOUT)
            except (ValueError, UnicodeError):
                finished_at = utc_now()
                failure = ProviderError(ErrorKind.SCHEMA)
            except OSError:
                finished_at = utc_now()
                failure = ProviderError(ErrorKind.UNAVAILABLE)

            # Determine billing status
            billing_status = "unknown"
            if http_status is not None:
                # If we got a status code, the server rejected it or failed it, but usage isn't returned for non-200s in interactions API usually
                billing_status = "unbilled" if http_status >= 400 else "unknown"

            # Retry logic
            if failure.kind in {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE} and attempts < 4:
                log_physical(http_status, "failed", failure.kind.value, False, billing_status, started_at, finished_at)
                delay = getattr(failure, 'retry_after', None)
                if not delay:
                    delay = backoffs[attempts] + random.uniform(0, 2)
                # DO NOT print raw messages to avoid leak
                self.sleeper(delay)
                attempts += 1
                GeminiTTSProvider._last_request_time = self.clock() - interval
                continue
                
            log_physical(http_status, "failed", failure.kind.value, False, billing_status, started_at, finished_at)
            raise failure from None

    def health_check(self):
        try:
            metadata = self._request()
            if (metadata.get('name') != 'models/' + self.model
                    or 'generateContent' not in metadata.get('supportedGenerationMethods', [])):
                raise ProviderError(ErrorKind.INPUT)
            self.last_status = ProviderStatus(provider=self.name, model=self.model, availability='available')
        except ProviderError as failure:
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
        return self.last_status



    def synthesize_segment(self, segment: SpeechSegment, destination: Path) -> SegmentSpeechInfo:
        from ..speech import normalize_tts_text
        self.last_usage, self.reported_model = None, None
        segment = SpeechSegment.model_validate(segment)
        voices = {'主持人': self.settings.host_voice, '嘉宾': self.settings.guest_voice}
        selected = {t.speaker: voices[t.speaker] for t in segment.turns}
        
        parts = []
        for t in segment.turns:
            normalized = normalize_tts_text(t.text)
            if not normalized.strip():
                continue
                
            if t.speaker == '主持人':
                style = self.settings.host_style or self.settings.style_instruction or "Natural Mandarin Chinese podcast host. Calm, thoughtful and conversational. Speak like a real podcast host discussing literature, not like a news anchor, audiobook narrator or advertisement. Use natural pauses and restrained emotion. Medium speaking pace."
            else:
                style = self.settings.guest_style or self.settings.style_instruction or "Natural Mandarin Chinese podcast guest. Relaxed, reflective and conversational. Respond naturally to the host rather than reading a script. Use subtle emotion and natural pauses. Medium speaking pace."

            parts.append({
                'type': 'text',
                'text': normalized,
                'annotations': [{
                    'type': 'speech_metadata',
                    'speaker': 'Host' if t.speaker == '主持人' else 'Guest',
                    'style': style
                }]
            })
            
        if not parts:
            raise ProviderError(ErrorKind.INPUT)
            
        payload = {
            'model': self.model,
            'input': [{
                'type': 'user_input',
                'content': parts
            }],
            'response_format': {
                'type': 'audio'
            },
            'generation_config': {
                'speech_config': {
                    'mode': getattr(self.settings, 'mode', 'conversational'),
                    'speakers': [
                        {'speaker': 'Host', 'voice': self.settings.host_voice},
                        {'speaker': 'Guest', 'voice': self.settings.guest_voice}
                    ]
                }
            }
        }
        
        try:
            print(f"云端 TTS: 发送 Interactions API 至 {self.model}", file=sys.stderr)
        except OSError:
            pass
            
        try:
            result = self._request(payload, destination)
            # Try to grab usage metadata if present in interactions API
            usage = result.get('usage_metadata') or result.get('usageMetadata')
            if isinstance(usage, dict):
                self.last_usage = ProviderUsage.from_response({
                    'prompt_tokens': usage.get('prompt_token_count', usage.get('promptTokenCount')),
                    'completion_tokens': usage.get('candidates_token_count', usage.get('candidatesTokenCount'))
                })
            
            decode_audio(result, destination)

            
        except ProviderError as failure:
            self.last_status = ProviderStatus.from_error(self.name, self.model, failure)
            raise
            
        self.last_status = ProviderStatus(provider=self.name, model=self.model, availability='available')
        return SegmentSpeechInfo(voices=selected)

    def synthesize(self, script, destination):

        # Never hide multiple cloud requests behind a single legacy Attempt.
        raise ProviderError(ErrorKind.INPUT)





@dataclass(frozen=True)
class AudioPayload:
    audio_bytes: bytes
    container: str = "wav"
    codec: str = "pcm_s16le"
    sample_rate: int = 24000
    channels: int = 1

    def __init__(
        self,
        audio_bytes: bytes | None = None,
        container: str = "wav",
        codec: str = "pcm_s16le",
        sample_rate: int = 24000,
        channels: int = 1,
        *,
        data: bytes | None = None,
        bytes: bytes | None = None,
    ):
        raw = audio_bytes if audio_bytes is not None else (data if data is not None else bytes)
        if raw is None:
            raise ValueError("audio_bytes is required")
        object.__setattr__(self, "audio_bytes", raw)
        object.__setattr__(self, "container", container)
        object.__setattr__(self, "codec", codec)
        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "channels", channels)

    @property
    def bytes(self) -> bytes:
        return self.audio_bytes

    @property
    def data(self) -> bytes:
        return self.audio_bytes


def decode_audio(result: dict, destination: Path | str | None = None) -> AudioPayload:
    if not isinstance(result, dict):
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason="response is not a dict",
        )

    status = result.get('status', 'completed')
    if status in {'failed', 'incomplete', 'cancelled'}:
        raw_reason = (
            result.get('error', {}).get('message', '')
            if isinstance(result.get('error'), dict)
            else ''
        ) or result.get('incomplete_reason', '') or result.get('reason', '')
        safe_reason = sanitize_gemini_error(str(raw_reason))
        raise ProviderError(
            ErrorKind.BUSINESS,
            error_type=f"interaction_{status}",
            validation_reason=safe_reason,
        )

    audio_items = []
    if 'steps' in result and isinstance(result['steps'], list):
        for step in result['steps']:
            if isinstance(step, dict):
                content = step.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'audio':
                            audio_items.append(item)
    else:
        if 'output_audio' in result and isinstance(result['output_audio'], dict):
            audio_items.append(result['output_audio'])
        elif 'output' in result and isinstance(result['output'], dict) and 'audio' in result['output'] and isinstance(result['output']['audio'], dict):
            audio_items.append(result['output']['audio'])
        elif 'candidates' in result and isinstance(result['candidates'], list):
            for cand in result['candidates']:
                if isinstance(cand, dict):
                    parts = cand.get('content', {}).get('parts', [])
                    if isinstance(parts, list):
                        for part in parts:
                            if isinstance(part, dict) and 'inlineData' in part and isinstance(part['inlineData'], dict):
                                audio_items.append(part['inlineData'])

    if not audio_items:
        reason = (
            "completed interaction contained no audio content"
            if status == 'completed'
            else "missing audio in interaction response"
        )
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason=reason,
        )

    if len(audio_items) > 1:
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason="multiple audio blocks found in interaction response",
        )

    audio_item = audio_items[0]

    raw_b64 = audio_item.get('data')
    if not isinstance(raw_b64, str) or not raw_b64.strip():
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason="missing audio data in audio item",
        )

    try:
        audio_bytes = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason="invalid base64 encoding",
        )

    mime_str = audio_item.get('mime_type') or audio_item.get('mimeType')
    is_raw_pcm = False
    if mime_str is not None:
        if not isinstance(mime_str, str):
            raise ProviderError(
                ErrorKind.SCHEMA,
                error_type="decode_error",
                validation_reason="invalid mime_type format",
            )
        mime_base = mime_str.lower().split(';')[0].strip()
        if mime_base in {'audio/wav', 'audio/x-wav', 'audio/wave', 'audio/vnd.wave', 'audio/*'}:
            pass
        elif mime_base in {'audio/l16', 'audio/pcm'}:
            is_raw_pcm = True
        else:
            raise ProviderError(
                ErrorKind.SCHEMA,
                error_type="decode_error",
                validation_reason=f"unsupported audio mime type: {mime_base}",
            )

    if is_raw_pcm and not audio_bytes.startswith(b'RIFF'):
        if len(audio_bytes) == 0 or len(audio_bytes) % 2 != 0:
            raise ProviderError(
                ErrorKind.SCHEMA,
                error_type="decode_error",
                validation_reason="invalid raw PCM length",
            )
        out = io.BytesIO()
        with wave.open(out, 'wb') as wav_out:
            wav_out.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav_out.writeframes(audio_bytes)
        audio_bytes = out.getvalue()

    if not audio_bytes.startswith(b'RIFF'):
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason="invalid RIFF header",
        )

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sampwidth = wav_file.getsampwidth()
            framerate = wav_file.getframerate()
            nframes = wav_file.getnframes()

            if (channels, sampwidth, framerate) != (1, 2, 24_000):
                raise ProviderError(
                    ErrorKind.SCHEMA,
                    error_type="decode_error",
                    validation_reason=f"unexpected WAV format: channels={channels}, sampwidth={sampwidth}, framerate={framerate}",
                )
            if nframes == 0:
                raise ProviderError(
                    ErrorKind.SCHEMA,
                    error_type="decode_error",
                    validation_reason="audio frames count is 0",
                )
            read_bytes = wav_file.readframes(nframes)
            if len(read_bytes) != nframes * sampwidth * channels:
                raise ProviderError(
                    ErrorKind.SCHEMA,
                    error_type="decode_error",
                    validation_reason="audio data is truncated",
                )
    except (wave.Error, EOFError, struct.error):
        raise ProviderError(
            ErrorKind.SCHEMA,
            error_type="decode_error",
            validation_reason="invalid WAV container or structure",
        ) from None

    payload = AudioPayload(
        audio_bytes=audio_bytes,
        container="wav",
        codec="pcm_s16le",
        sample_rate=framerate,
        channels=channels,
    )

    if destination is not None:
        dest_path = Path(destination)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(payload.audio_bytes)

    return payload
