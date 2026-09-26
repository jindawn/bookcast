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
import sqlite3
import struct
import sys
import time
from email.utils import parsedate_to_datetime
from collections.abc import Callable
from urllib import error, request
import wave

from ..generation import ProviderUsage
from ..provider_api import (ErrorKind, ProviderCapabilities, ProviderError, ProviderStatus,
                            ProviderRequestContext, SpeechSegment, SegmentSpeechInfo)
from ..storage import fingerprint

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
WIRE_VERSION = "gemini-generate-content-tts-v1:pcm-s16le-24k"
MAX_RESPONSE = 32 * 1024 * 1024

def sanitize_gemini_error(text: str) -> str:
    """Never retain upstream prose, including a supposedly harmless prefix."""
    return "UNKNOWN"


SAFE_REASONS = frozenset({
    'AUTH', 'PERMISSION', 'RATE_LIMIT', 'QUOTA', 'TIMEOUT', 'UNAVAILABLE',
    'INVALID_REQUEST', 'DECODE_ERROR', 'UNKNOWN',
})
REASON_BY_KIND = {
    ErrorKind.AUTH: 'AUTH', ErrorKind.PERMISSION: 'PERMISSION',
    ErrorKind.RATE_LIMIT: 'RATE_LIMIT', ErrorKind.QUOTA: 'QUOTA',
    ErrorKind.TIMEOUT: 'TIMEOUT', ErrorKind.UNAVAILABLE: 'UNAVAILABLE',
    ErrorKind.INPUT: 'INVALID_REQUEST', ErrorKind.SCHEMA: 'DECODE_ERROR',
}


def safe_failure(failure: ProviderError) -> ProviderError:
    """Constrain every value that can reach an Attempt, event, or Web DTO."""
    internal_reason = (failure.error_type if failure.error_type == failure.validation_reason
                       and failure.error_type in SAFE_REASONS else None)
    reason = internal_reason or REASON_BY_KIND.get(failure.kind, 'UNKNOWN')
    if reason not in SAFE_REASONS:
        reason = 'UNKNOWN'
    result = ProviderError(failure.kind, error_type=reason, validation_reason=reason)
    result.retry_after = getattr(failure, 'retry_after', None)
    result.quota_reason = getattr(failure, 'quota_reason', None)
    return result


def _retry_after_seconds(headers, wall_clock: Callable[[], float]) -> float | None:
    if not headers:
        return None
    value = headers.get('Retry-After')
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            return max(0.0, date.timestamp() - wall_clock())
        except (ValueError, TypeError, OverflowError, IndexError):
            return None

def classify_http(status, body, headers=None, *, wall_clock=time.time):
    """Use structured status/reasons/quota IDs, never persist or match error prose."""
    try:
        document = json.loads(body)
        failure = document.get('error', {}) if isinstance(document, dict) else {}
        failure = failure if isinstance(failure, dict) else {}
        details = failure.get('details', [])
        details = [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []
        reasons = {d.get('reason') for d in details if isinstance(d.get('reason'), str)}
    except (ValueError, AttributeError, TypeError):
        failure, details, reasons = {}, [], set()
        
    upstream_status = failure.get('status')
    status_code = status if type(status) is int else 0
    quota_reason = None

    kind = ErrorKind.BUSINESS
    if status_code == 401 or upstream_status == 'UNAUTHENTICATED' or reasons & {'API_KEY_INVALID', 'API_KEY_EXPIRED'}:
        kind = ErrorKind.AUTH
    elif status_code == 403:
        kind = ErrorKind.PERMISSION
    elif status_code == 402 or reasons & {'BILLING_DISABLED', 'BILLING_NOT_ACTIVE', 'QUOTA_EXCEEDED'}:
        kind = ErrorKind.QUOTA
        quota_reason = 'BILLING' if reasons & {'BILLING_DISABLED', 'BILLING_NOT_ACTIVE'} else 'QUOTA'
    elif status_code == 429:
        kind = ErrorKind.RATE_LIMIT
        msg = str(failure.get('message', '')).lower()
        is_daily = any(kw in msg for kw in ('per day', 'per_day', 'requests per day', 'daily'))
        violations = [v for d in details for v in (d.get('violations') if isinstance(d.get('violations'), list) else [])
                      if isinstance(v, dict)]
        for violation in violations:
            quota_id = str(violation.get('quotaId', '')).lower()
            if ('perday' in quota_id or 'per_day' in quota_id
                    or violation.get('quotaValue') in (0, '0')):
                is_daily = True
                break
        if is_daily:
            kind = ErrorKind.QUOTA
            quota_reason = 'DAILY_LIMIT'
    elif status_code in {408, 504}:
        kind = ErrorKind.TIMEOUT
    elif status_code >= 500:
        kind = ErrorKind.UNAVAILABLE
    else:
        kind = ErrorKind.SCHEMA

    if kind == ErrorKind.SCHEMA:
        err = ProviderError(kind, error_type='INVALID_REQUEST', validation_reason='INVALID_REQUEST')
    else:
        err = safe_failure(ProviderError(kind))
    
    retry_after = _retry_after_seconds(headers, wall_clock)
    if retry_after is None:
        raw_msg = str(failure.get('message', ''))
        match = re.search(r'retry\s+(?:in|after)\s+([0-9]+(?:\.[0-9]+)?)\s*s', raw_msg, re.IGNORECASE)
        if match:
            try:
                retry_after = float(match.group(1))
            except ValueError:
                pass
        if retry_after is None:
            for d in details:
                delay_str = d.get('retryDelay')
                if isinstance(delay_str, str) and delay_str.endswith('s'):
                    try:
                        retry_after = float(delay_str[:-1])
                        break
                    except ValueError:
                        pass

    err.retry_after = retry_after
    err.quota_reason = quota_reason
    return err



class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError(ErrorKind.INPUT)


class GeminiRequestLimiter:
    """Atomic slots shared by every local process using this SQLite database.

    Monotonic values are comparable across processes on one boot. A lower value
    after reboot resets stale state; an undetected old reservation is finite.
    A wall-clock adjustment cannot advance a reservation.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic):
        self.path = Path(path)
        self.clock = clock

    def next_deadline(self) -> float:
        with sqlite3.connect(self.path, timeout=5) as db:
            row = db.execute('SELECT next_at, observed_at FROM slot WHERE id = 1').fetchone()
        now = self.clock()
        return max(now, row[0]) if row and now >= row[1] else now

    def reserve_next_slot(self, interval: float) -> float:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS slot (id INTEGER PRIMARY KEY CHECK (id = 1), '
                       'next_at REAL NOT NULL, observed_at REAL NOT NULL, last_sent_at REAL)')
            row = db.execute('SELECT next_at, observed_at, last_sent_at FROM slot WHERE id = 1').fetchone()
            now = self.clock()
            # A lower observed monotonic value means a reboot. Future slots
            # reserved by other live workers must never be mistaken for one.
            previous = row[0] if row else now
            rebooted = bool(row and now < row[1])
            if rebooted:
                previous = now
            send_at = max(now, previous)
            last_sent = None if rebooted or not row else row[2]
            db.execute('INSERT INTO slot (id, next_at, observed_at, last_sent_at) VALUES (1, ?, ?, ?) '
                       'ON CONFLICT(id) DO UPDATE SET next_at = excluded.next_at, '
                       'observed_at = excluded.observed_at, last_sent_at = excluded.last_sent_at',
                       (send_at + interval, now, last_sent))
        return send_at

    def claim_send_slot(self, interval: float) -> float:
        """Recheck at send time so a delayed worker cannot crowd a later one."""
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT observed_at, last_sent_at FROM slot WHERE id = 1').fetchone()
            now = self.clock()
            previous = None if row is None or now < row[0] else row[1]
            allowed_at = max(now, previous + interval) if previous is not None else now
            if allowed_at <= now:
                db.execute('UPDATE slot SET last_sent_at = ?, observed_at = ? WHERE id = 1', (now, now))
            else:
                db.execute('UPDATE slot SET observed_at = ? WHERE id = 1', (now,))
        return allowed_at


class GeminiTTSProvider:
    def __init__(self, spec, *, clock: Callable[[], float] | None = None,
                 sleeper: Callable[[float], None] | None = None,
                 limiter: GeminiRequestLimiter | None = None,
                 wall_clock: Callable[[], float] | None = None):
        self.spec, self.name, self.model = spec, spec.name, spec.model
        self.clock = clock if clock is not None else time.monotonic
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.wall_clock = wall_clock if wall_clock is not None else time.time
        limiter_path = Path(os.environ.get('BOOKCAST_GEMINI_LIMITER_PATH',
                                        str(Path.home() / '.bookcast' / 'gemini_tts_slots.sqlite3')))
        self.limiter = limiter if limiter is not None else GeminiRequestLimiter(limiter_path, clock=self.clock)
        self.settings = spec.cloud_tts
        self.last_status = ProviderStatus(provider=self.name, model=self.model)
        self.last_usage = None
        self.reported_model = None
        self.telemetry_degraded = False

    def capabilities(self):
        return ProviderCapabilities(speech=True, speech_segments=True, multi_speaker=True, cloud=True)

    @property
    def cache_key(self):
        return fingerprint({'adapter': WIRE_VERSION, 'model': self.model, 'endpoint': ENDPOINT,
                            'settings': self.settings.model_dump()})

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
            self.telemetry_degraded = True
            if context.on_telemetry_degraded:
                try:
                    context.on_telemetry_degraded('PHYSICAL_REQUEST_LOG_UNAVAILABLE')
                except Exception:
                    pass

    def _request(self, payload=None, destination=None, *, request_context: ProviderRequestContext | None = None):
        from ..models import utc_now
        key = os.environ.get(self.spec.api_key_env)
        if not key or not key.strip():
            raise safe_failure(ProviderError(ErrorKind.AUTH))
        url = ENDPOINT + self.model if payload is None else 'https://generativelanguage.googleapis.com/v1beta/interactions'

        if request_context and (request_context.provider != self.name or request_context.model != self.model):
            raise safe_failure(ProviderError(ErrorKind.INPUT))
        attempts = 0
        backoffs = [10, 20, 40, 60]
        interval = max(25, self.settings.min_request_interval) if payload is not None else 25
        retry_deadline = self.clock()
        while True:
            if payload is not None:
                try:
                    if retry_deadline > self.clock():
                        # Wait locally before reserving. A crashed Retry-After
                        # cannot strand a far-future shared slot.
                        due = max(retry_deadline, self.limiter.next_deadline())
                        delay = max(0.0, due - self.clock())
                        if delay:
                            self.sleeper(delay)
                    send_at = self.limiter.reserve_next_slot(interval)
                except (OSError, sqlite3.Error):
                    raise safe_failure(ProviderError(ErrorKind.UNAVAILABLE)) from None
                delay = max(0.0, send_at - self.clock())
                if delay:
                    self.sleeper(delay)
                while True:
                    try:
                        allowed_at = self.limiter.claim_send_slot(interval)
                    except (OSError, sqlite3.Error):
                        raise safe_failure(ProviderError(ErrorKind.UNAVAILABLE)) from None
                    delay = max(0.0, allowed_at - self.clock())
                    if not delay:
                        break
                    self.sleeper(delay)

            started_at = utc_now()
            http_status = None
            usage_available = False
            billing_evidence = 'unknown'
            try:
                req = request.Request(url, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                    data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
                with request.build_opener(NoRedirect()).open(req, timeout=self.spec.timeout_seconds) as response:
                    http_status = getattr(response, 'getcode', lambda: 200)()
                    raw = response.read(MAX_RESPONSE + 1)
                finished_at = utc_now()
                if len(raw) > MAX_RESPONSE:
                    raise safe_failure(ProviderError(ErrorKind.SCHEMA))
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise safe_failure(ProviderError(ErrorKind.SCHEMA))
                usage_metadata = value.get('usage_metadata') or value.get('usageMetadata')
                usage_available = isinstance(usage_metadata, dict) and any(
                    type(usage_metadata.get(field)) is int and usage_metadata[field] >= 0
                    for field in ('prompt_token_count', 'promptTokenCount',
                                  'candidates_token_count', 'candidatesTokenCount'))
                billing_evidence = 'confirmed' if usage_available else 'unknown'
                self._record_physical(request_context, self._physical_record(
                    request_context, attempts, started_at, finished_at, http_status,
                    'success', None, usage_available, billing_evidence))
                return value
            except error.HTTPError as exc:
                finished_at = utc_now()
                http_status = exc.code
                try:
                    error_body = exc.read(64 * 1024)
                except OSError:
                    error_body = b'{}'
                failure = classify_http(exc.code, error_body, exc.headers,
                                        wall_clock=self.wall_clock)
            except error.URLError as exc:
                finished_at = utc_now()
                failure = safe_failure(ProviderError(
                    ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE))
            except TimeoutError:
                finished_at = utc_now()
                failure = safe_failure(ProviderError(ErrorKind.TIMEOUT))
            except (ValueError, UnicodeError):
                finished_at = utc_now()
                failure = safe_failure(ProviderError(ErrorKind.SCHEMA))
            except OSError:
                finished_at = utc_now()
                failure = safe_failure(ProviderError(ErrorKind.UNAVAILABLE))
            except ProviderError as exc:
                finished_at = utc_now()
                failure = safe_failure(exc)

            self._record_physical(request_context, self._physical_record(
                request_context, attempts, started_at, finished_at, http_status,
                'failed', failure.kind.value, False, 'unknown',
                getattr(failure, 'quota_reason', None), failure.kind.value,
                failure.error_type, failure.retryable))
            if failure.kind in {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE} and attempts < 4:
                delay = getattr(failure, 'retry_after', None)
                if delay is None:
                    delay = backoffs[attempts] + random.uniform(0, 2)
                retry_deadline = self.clock() + delay
                attempts += 1
                continue
            raise failure from None

    def _physical_record(self, context, attempt, started, finished, status, result,
                         retry_reason, usage_available, billing_evidence, quota_reason=None,
                         error_kind=None, safe_reason=None, retryable=None):
        return {
            'job_id': context.job_id if context else None,
            'output_id': context.output_id if context else None,
            'logical_chunk_id': context.logical_chunk_id if context else None,
            'chunk_id': context.logical_chunk_id if context else None,
            'provider': self.name, 'model': self.model,
            'physical_attempt_index': attempt,
            'started_at': started, 'finished_at': finished,
            'http_status': status if type(status) is int else None,
            'result': result, 'retry_reason': retry_reason,
            'usage_available': usage_available,
            'billing_evidence': billing_evidence,
            'quota_reason': quota_reason,
            'error_kind': error_kind,
            'safe_reason': safe_reason,
            'retryable': retryable,
        }

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



    def synthesize_segment_with_context(self, segment: SpeechSegment, destination: Path,
                                        context: ProviderRequestContext) -> SegmentSpeechInfo:
        return self.synthesize_segment(segment, destination, request_context=context)

    def synthesize_segment(self, segment: SpeechSegment, destination: Path, *,
                           request_context: ProviderRequestContext | None = None) -> SegmentSpeechInfo:
        from ..speech import normalize_tts_text
        self.last_usage, self.reported_model = None, None
        self.telemetry_degraded = False
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
            result = (self._request(payload, destination, request_context=request_context)
                      if request_context is not None else self._request(payload, destination))
            # Try to grab usage metadata if present in interactions API
            usage = result.get('usage_metadata') or result.get('usageMetadata')
            if isinstance(usage, dict):
                self.last_usage = ProviderUsage.from_response({
                    'prompt_tokens': usage.get('prompt_token_count', usage.get('promptTokenCount')),
                    'completion_tokens': usage.get('candidates_token_count', usage.get('candidatesTokenCount'))
                })
            
            decode_audio(result, destination)

            
        except ProviderError as failure:
            safe = safe_failure(failure)
            self.last_status = ProviderStatus.from_error(self.name, self.model, safe)
            raise safe from None
            
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
