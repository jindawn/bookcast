"""Optional official Gemini REST TTS. One request per Attempt, no SDK retries.

The documented generateContent TTS endpoint is stateless. No App cookies,
sessions, arbitrary endpoints, headers, or raw upstream failures are persisted.
"""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, request
import wave

from ..generation import ProviderUsage
from ..provider_api import (ErrorKind, ProviderCapabilities, ProviderError, ProviderStatus,
                            SpeechSegment, SegmentSpeechInfo)
from ..storage import fingerprint

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
WIRE_VERSION = "gemini-generate-content-tts-v1:pcm-s16le-24k"
MAX_RESPONSE = 32 * 1024 * 1024


def classify_http(status, body):
    """Use structured status/reasons/quota IDs, never persist or match error prose."""
    try:
        failure = json.loads(body).get('error', {})
        details = failure.get('details', [])
        details = [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []
        reasons = {d.get('reason') for d in details if isinstance(d.get('reason'), str)}
    except (ValueError, AttributeError, TypeError):
        failure, details, reasons = {}, [], set()
    if status == 401 or failure.get('status') == 'UNAUTHENTICATED' or reasons & {'API_KEY_INVALID', 'API_KEY_EXPIRED'}:
        return ProviderError(ErrorKind.AUTH)
    if status == 403:
        return ProviderError(ErrorKind.PERMISSION)
    if status == 402 or reasons & {'BILLING_DISABLED', 'BILLING_NOT_ACTIVE', 'QUOTA_EXCEEDED'}:
        return ProviderError(ErrorKind.QUOTA)
    if status == 429:
        violations = [v for d in details for v in (d.get('violations') if isinstance(d.get('violations'), list) else [])
                      if isinstance(v, dict)]
        for violation in violations:
            quota_id = str(violation.get('quotaId', '')).lower()
            if ('perday' in quota_id or 'per_day' in quota_id
                    or violation.get('quotaValue') in (0, '0')):
                return ProviderError(ErrorKind.QUOTA)
        return ProviderError(ErrorKind.RATE_LIMIT)

    if status in {408, 504}:
        return ProviderError(ErrorKind.TIMEOUT)
    if status >= 500:
        return ProviderError(ErrorKind.UNAVAILABLE)
        
    error_status = failure.get('status', str(status))
    error_msg = failure.get('message', '')
    if error_msg:
        import sys
        print(f"Gemini API Error: {error_status} - {error_msg[:300]}", file=sys.stderr)
    return ProviderError(ErrorKind.SCHEMA, error_type=error_status, validation_reason=error_msg[:300])



class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError(ErrorKind.INPUT)


class GeminiTTSProvider:
    def __init__(self, spec):
        self.spec, self.name, self.model = spec, spec.name, spec.model
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

    def _request(self, payload=None):
        key = os.environ.get(self.spec.api_key_env)
        if not key:
            sys.exit('Gemini TTS requires GEMINI_API_KEY')
        url = ENDPOINT + self.model if payload is None else 'https://generativelanguage.googleapis.com/v1beta/interactions'
        try:
            req = request.Request(url, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
            with request.build_opener(NoRedirect()).open(req, timeout=self.spec.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ProviderError(ErrorKind.SCHEMA)
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ProviderError(ErrorKind.SCHEMA)
            return value
        except error.HTTPError as exc:
            failure = classify_http(exc.code, exc.read(64 * 1024))
        except error.URLError as exc:
            failure = ProviderError(ErrorKind.TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorKind.UNAVAILABLE)
        except TimeoutError:
            failure = ProviderError(ErrorKind.TIMEOUT)
        except (ValueError, UnicodeError):
            failure = ProviderError(ErrorKind.SCHEMA)
        except OSError:
            failure = ProviderError(ErrorKind.UNAVAILABLE)
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
                
            style = "calm, thoughtful Chinese podcast host" if t.speaker == '主持人' else "natural, conversational, reflective guest"
            if self.settings.style_instruction:
                style = self.settings.style_instruction

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
            result = self._request(payload)
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





def decode_audio(result, destination):
    status = result.get('status', 'completed')
    if status in {'failed', 'incomplete', 'cancelled'}:
        reason = result.get('error', {}).get('message', '') or result.get('incomplete_reason', '') or result.get('reason', '')
        raise ProviderError(ErrorKind.BUSINESS, error_type=f"interaction_{status}", validation_reason=reason)

    data = None
    
    # 1. New REST API schema: steps[].content[]
    if 'steps' in result:
        for step in result['steps']:
            for item in step.get('content', []):
                if item.get('type') == 'audio' and 'data' in item:
                    data = base64.b64decode(item['data'], validate=True)
                    break
            if data:
                break
                
    # 2. Known convenience-compatible shape / legacy outputs
    if not data:
        if 'output_audio' in result and 'data' in result['output_audio']:
            data = base64.b64decode(result['output_audio']['data'], validate=True)
        elif 'output' in result and isinstance(result['output'], dict) and 'audio' in result['output']:
            data = base64.b64decode(result['output']['audio']['data'], validate=True)
        elif 'candidates' in result:
            try:
                inline = result['candidates'][0]['content']['parts'][0]['inlineData']
                data = base64.b64decode(inline['data'], validate=True)
            except (KeyError, IndexError, TypeError):
                pass
                
    if not data:
        import sys
        keys = list(result.keys())
        steps_info = []
        for i, step in enumerate(result.get('steps', [])):
            stype = step.get('type', 'unknown')
            ctypes = [c.get('type', 'unknown') for c in step.get('content', [])]
            steps_info.append(f"step[{i}].type={stype} content_types={ctypes}")
            
        print(f"status={status}", file=sys.stderr)
        print(f"top_level_keys={keys}", file=sys.stderr)
        print(f"steps={len(result.get('steps', []))}", file=sys.stderr)
        for detail in steps_info:
            print(detail, file=sys.stderr)
            
        if status == 'completed':
            raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error", validation_reason="completed interaction contained no audio content")
        raise ProviderError(ErrorKind.SCHEMA, error_type="decode_error", validation_reason="No audio data found in response schema")

    # 3. Direct WAV output without re-wrapping
    if not data.startswith(b'RIFF'):
        import sys
        print("Warning: Decoded audio data does not start with 'RIFF'. Proceeding anyway.", file=sys.stderr)
        
    with open(str(destination), 'wb') as output:
        output.write(data)


def decode_pcm(result):

    try:
        candidates = result['candidates']
        if len(candidates) != 1 or candidates[0].get('finishReason') != 'STOP':
            raise ValueError()
        parts = candidates[0]['content']['parts']
        if len(parts) != 1:
            raise ValueError()
        inline = parts[0]['inlineData']
        mime, *parameters = inline['mimeType'].lower().replace(' ', '').split(';')
        fields = dict(p.split('=', 1) for p in parameters)
        if (mime != 'audio/l16' or len(fields) != len(parameters)
                or fields.get('rate') != '24000' or fields.get('codec', 'pcm') != 'pcm'
                or fields.get('channels', '1') != '1'
                or set(fields) - {'rate', 'codec', 'channels'}):
            raise ValueError()
        pcm = base64.b64decode(inline['data'], validate=True)
        if not 0 < len(pcm) <= 24000 * 2 * 480 or len(pcm) % 2 or not any(pcm):
            raise ValueError()
        # Google's documented L16 TTS bytes are PCM s16le (not RFC big endian).
        return pcm
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, binascii.Error):
        raise ProviderError(ErrorKind.SCHEMA) from None
