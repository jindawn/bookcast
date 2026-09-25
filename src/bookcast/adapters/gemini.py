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
    return ProviderError(ErrorKind.INPUT)


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
        url = ENDPOINT + self.model + (':generateContent' if payload is not None else '')
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
            # Overwrite if general style instruction is provided
            if self.settings.style_instruction:
                style = self.settings.style_instruction

            parts.append({
                'text': normalized,
                'speech_metadata': {
                    'speaker': voices[t.speaker],
                    'style': style
                }
            })
            
        if not parts:
            raise ProviderError(ErrorKind.INPUT)
            
        payload = {
            'contents': [{'parts': parts}],
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {
                    'mode': getattr(self.settings, 'mode', 'conversational')
                }
            }
        }
        
        try:
            print('云端 TTS：将播客文本发送至 Google Developer API；免费/未知层可能用于产品改进，付费层适用不同数据条款。',
                  file=sys.stderr)
        except OSError:
            pass  # A detached terminal must not invalidate a durable job.
            
        try:
            result = self._request(payload)
            usage = result.get('usageMetadata')
            if isinstance(usage, dict):
                self.last_usage = ProviderUsage.from_response({
                    'prompt_tokens': usage.get('promptTokenCount'),
                    'completion_tokens': usage.get('candidatesTokenCount')
                })
            model = result.get('modelVersion')
            if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,128}', model):
                self.reported_model = model
                
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
    try:
        candidates = result['candidates']
        if len(candidates) != 1 or candidates[0].get('finishReason') != 'STOP':
            raise ValueError()
        parts = candidates[0]['content']['parts']
        if len(parts) != 1:
            raise ValueError()
        inline = parts[0]['inlineData']
        mime = inline['mimeType'].lower().replace(' ', '').split(';')[0]
        
        data = base64.b64decode(inline['data'], validate=True)
        if not data:
            raise ValueError()
            
        if mime == 'audio/wav':
            with open(str(destination), 'wb') as output:
                output.write(data)
        elif mime == 'audio/l16':
            with wave.open(str(destination), 'wb') as output:
                output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                output.writeframes(data)
        else:
            raise ValueError()
            
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, binascii.Error):
        raise ProviderError(ErrorKind.SCHEMA) from None

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
